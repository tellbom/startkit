from __future__ import annotations

import pandas as pd

from stock_strategy_api.market_data.parquet_store import write_ohlcv
from stock_strategy_api.market_data.paths import ohlcv_path
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.services.backtest_service import BacktestService, CostConfig
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy
from tests.integration.test_scan_pipeline import _install_fixture


def test_event_backtest_enters_d4_and_respects_t1(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    service = BacktestService(tmp_path, runs, costs=CostConfig())
    d3 = calendar.nth_trading_day_after(d0, 3)
    result = service.run(StrongGapUpStrategy(), d0, d3)
    stored = runs.get_backtest(result["run_id"])
    assert stored is not None
    assert stored["status"] == "success"
    events, total = runs.backtest_events(result["run_id"], 10, 0)
    assert total == 1
    event = events[0]
    assert event["entry_date"] == calendar.nth_trading_day_after(d0, 4).isoformat()
    assert event["exit_date"] > event["entry_date"]
    assert event["net_return"] < event["gross_return"]
    assert stored["production_verified"] is False
    assert stored["metrics"]["security_master_pit_coverage"] == 0.25
    assert event["security_master_pit"] is True
    assert event["backtest_quality"]["security_master_pit_coverage"] == 0.25
    assert len(event["backtest_quality"]["security_master_pit_missing_dates"]) == 3
    with database.connect() as connection:
        metric_rows = connection.execute(
            "SELECT COUNT(*) count FROM backtest_metrics WHERE run_id=?", (result["run_id"],)
        ).fetchone()["count"]
    assert metric_rows > 0


def test_entry_day_fill_exits_next_trading_day(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    raw_path = ohlcv_path(tmp_path, "600000", "raw")
    raw = pd.read_parquet(raw_path)
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    d4 = calendar.nth_trading_day_after(d0, 4)
    floor = float(qualifying_frames[0].iloc[-2]["high"])
    raw.loc[raw["date"] == d4, "low"] = floor
    write_ohlcv(raw, raw_path, "600000")

    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    d3 = calendar.nth_trading_day_after(d0, 3)
    result = BacktestService(tmp_path, runs).run(StrongGapUpStrategy(), d0, d3)
    event = runs.backtest_events(result["run_id"], 10, 0)[0][0]
    assert event["entry_date"] == d4.isoformat()
    assert event["exit_reason"] == "full_fill_next_open"
    assert event["exit_date"] == calendar.next_trading_day(d4).isoformat()


def test_one_price_up_days_are_not_fabricated_entries(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    raw_path = ohlcv_path(tmp_path, "600000", "raw")
    raw = pd.read_parquet(raw_path)
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    for offset in (4, 5, 6):
        day = calendar.nth_trading_day_after(d0, offset)
        previous_close = float(raw.loc[raw["date"] < day].sort_values("date").iloc[-1]["close"])
        raw.loc[raw["date"] == day, ["open", "high", "low", "close"]] = previous_close + 1
    write_ohlcv(raw, raw_path, "600000")

    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    result = BacktestService(tmp_path, runs).run(StrongGapUpStrategy(), d0, calendar.nth_trading_day_after(d0, 3))
    event = runs.backtest_events(result["run_id"], 10, 0)[0][0]
    assert event["status"] == "unfilled_entry"
    assert event["entry_date"] is None


def test_complete_pit_fixture_passes_production_quality_gate(tmp_path, qualifying_frames, d0):
    _install_fixture(tmp_path, qualifying_frames, d0)
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    result = BacktestService(tmp_path, runs).run(StrongGapUpStrategy(), d0, d0)
    stored = runs.get_backtest(result["run_id"])
    assert stored is not None
    assert stored["metrics"]["universe_mode"] == "point_in_time"
    assert stored["metrics"]["security_master_pit_coverage"] == 1.0
    assert stored["production_verified"] is True
