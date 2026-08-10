from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from stock_strategy_api.core.config import Settings
from stock_strategy_api.core.errors import DataUnavailableError
from stock_strategy_api.main import create_app
from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.market_data.parquet_store import write_ohlcv
from stock_strategy_api.market_data.paths import ohlcv_path
from stock_strategy_api.market_data.security_master import SecurityMasterService
from stock_strategy_api.market_data.universe import UniverseService
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.services.backtest_service import BacktestService
from stock_strategy_api.services.scan_service import ScanService
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy
from tests.conftest import build_calendar_frame


def _install_fixture(data_root, qualifying_frames, d0):
    raw, qfq = qualifying_frames
    calendar = CalendarService(data_root)
    calendar.save_fixture(build_calendar_frame(d0 - dt.timedelta(days=90), periods=100))
    future_days = [calendar.nth_trading_day_after(d0, index) for index in range(1, 16)]
    raw_future = []
    for index, date in enumerate(future_days, start=1):
        price = 11.45 + index * 0.02
        raw_future.append(
            {
                "symbol": "600000",
                "date": date,
                "open": price,
                "high": price + 0.1,
                "low": 10.75,
                "close": price + 0.05,
                "volume": 120,
                "amount": 1200,
            }
        )
    raw = pd.concat([raw, pd.DataFrame(raw_future)], ignore_index=True)
    qfq_future = pd.DataFrame(raw_future)
    for column in ("open", "high", "low", "close"):
        qfq_future[column] *= 1.1
    qfq = pd.concat([qfq, qfq_future], ignore_index=True)
    write_ohlcv(raw, ohlcv_path(data_root, "600000", "raw"), "600000")
    write_ohlcv(qfq, ohlcv_path(data_root, "600000", "qfq"), "600000")

    UniverseService(data_root).save_fixture(
        pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "name": "浦发银行",
                    "in_date": d0 - dt.timedelta(days=1000),
                    "out_date": None,
                    "source": "point_in_time",
                    "snapshot_date": d0,
                }
            ]
        )
    )
    SecurityMasterService(data_root).save_fixture(
        pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "name": "浦发银行",
                    "exchange": "sh",
                    "listing_date": d0 - dt.timedelta(days=1000),
                    "status": "active",
                    "effective_date": d0,
                    "source": "fixture",
                }
            ]
        ),
        d0,
    )
    return calendar


def test_scan_then_d1_confirmation(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    signals = SignalRepository(database)
    service = ScanService(tmp_path, RunRepository(database), signals)
    strategy = StrongGapUpStrategy()
    result = service.scan(strategy, d0)
    assert result["triggered"] == 1
    replay = service.scan(strategy, d0)
    assert replay["run_id"] == result["run_id"]
    assert replay["idempotent_replay"] is True
    d1 = calendar.next_trading_day(d0)
    assert service.advance(strategy, d1)["updated"] == 1
    rows, total = signals.list_signals(state="entry_eligible", include_exhaustion=True)
    assert total == 1
    assert rows[0].earliest_entry_date == calendar.nth_trading_day_after(d0, 2)


def test_fixture_full_chain_scan_api_and_t1_backtest(tmp_path, qualifying_frames, d0):
    data_root = tmp_path / "data"
    calendar = _install_fixture(data_root, qualifying_frames, d0)
    database_path = data_root / "strategy.sqlite3"
    database = Database(database_path)
    database.initialize()
    runs = RunRepository(database)
    signals = SignalRepository(database)
    strategy = StrongGapUpStrategy()
    scanner = ScanService(data_root, runs, signals)

    assert scanner.scan(strategy, d0)["triggered"] == 1
    d1 = calendar.next_trading_day(d0)
    assert scanner.advance(strategy, d1)["updated"] == 1

    settings = Settings(data_dir=data_root, database_path=database_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/recommendations")
        assert response.status_code == 200
        assert response.json()["meta"]["total"] == 1
        assert response.json()["data"][0]["state"] == "entry_eligible"
        assert response.json()["data"][0]["earliest_entry_date"] == calendar.nth_trading_day_after(d0, 2).isoformat()

    result = BacktestService(data_root, runs).run(strategy, d0, d1)
    event = runs.backtest_events(result["run_id"], 10, 0)[0][0]
    assert event["state_path"][-1]["state"] == "entry_eligible"
    assert event["entry_date"] == calendar.nth_trading_day_after(d0, 2).isoformat()
    assert event["exit_date"] > event["entry_date"]
    assert event["horizon_returns"]["1"]["net_return"] < event["horizon_returns"]["1"]["gross_return"]


def test_failed_later_scan_preserves_previous_success(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    service = ScanService(tmp_path, runs, SignalRepository(database))
    strategy = StrongGapUpStrategy()
    successful = service.scan(strategy, d0)

    d1 = calendar.next_trading_day(d0)
    qfq_path = ohlcv_path(tmp_path, "600000", "qfq")
    qfq = pd.read_parquet(qfq_path)
    qfq["date"] = pd.to_datetime(qfq["date"]).dt.date
    write_ohlcv(qfq.loc[qfq["date"] != d1], qfq_path, "600000")
    with pytest.raises(DataUnavailableError):
        service.scan(strategy, d1)
    latest = runs.latest_successful_scan(strategy.metadata().strategy_id)
    assert latest is not None
    assert latest["run_id"] == successful["run_id"]


def test_recent_scan_backfills_d0_even_when_latest_day_already_succeeded(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    signals = SignalRepository(database)
    service = ScanService(tmp_path, runs, signals)
    strategy = StrongGapUpStrategy()
    d1 = calendar.next_trading_day(d0)

    service.scan(strategy, d1)
    result = service.scan_recent(strategy, d1)

    assert result["lookback_trading_days"] == 4
    assert result["scanned_dates"] == [day.isoformat() for day in calendar.trading_days_ending_on(d1, 4)]
    rows, total = signals.list_signals(state="entry_eligible", include_exhaustion=True)
    assert total == 1
    assert rows[0].signal_date == d0
    assert rows[0].confirmation_date == d1
