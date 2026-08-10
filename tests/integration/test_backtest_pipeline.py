from __future__ import annotations

import pandas as pd

from stock_strategy_api.market_data.parquet_store import write_ohlcv
from stock_strategy_api.market_data.paths import ohlcv_path
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.services.backtest_service import BacktestService, CostConfig
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy
from stock_strategy_api.strategies.strong_gap_up_v1.config import StrongGapConfig
from tests.integration.test_scan_pipeline import _install_fixture


def test_event_backtest_enters_d2_and_respects_t1(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    service = BacktestService(tmp_path, runs, costs=CostConfig())
    d1 = calendar.next_trading_day(d0)
    result = service.run(StrongGapUpStrategy(), d0, d1)
    stored = runs.get_backtest(result["run_id"])
    assert stored is not None
    assert stored["status"] == "success"
    events, total = runs.backtest_events(result["run_id"], 10, 0)
    assert total == 1
    event = events[0]
    assert event["entry_date"] == calendar.nth_trading_day_after(d0, 2).isoformat()
    assert event["exit_date"] > event["entry_date"]
    assert event["net_return"] < event["gross_return"]
    assert stored["production_verified"] is False
    assert stored["metrics"]["security_master_pit_coverage"] == 0.5
    assert event["security_master_pit"] is True
    assert event["backtest_quality"]["security_master_pit_coverage"] == 0.5
    assert len(event["backtest_quality"]["security_master_pit_missing_dates"]) == 1
    assert set(event["horizon_returns"]) == {"1", "2", "3", "4", "5"}
    assert event["mfe"] >= event["mae"]
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
    d2 = calendar.nth_trading_day_after(d0, 2)
    floor = float(qualifying_frames[0].iloc[-2]["high"])
    raw.loc[raw["date"] == d2, "low"] = floor
    write_ohlcv(raw, raw_path, "600000")

    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    d1 = calendar.next_trading_day(d0)
    result = BacktestService(tmp_path, runs).run(StrongGapUpStrategy(), d0, d1)
    event = runs.backtest_events(result["run_id"], 10, 0)[0][0]
    assert event["entry_date"] == d2.isoformat()
    assert event["exit_reason"] == "full_fill_next_open"
    assert event["exit_date"] == calendar.next_trading_day(d2).isoformat()


def test_one_price_up_days_are_not_fabricated_entries(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    raw_path = ohlcv_path(tmp_path, "600000", "raw")
    raw = pd.read_parquet(raw_path)
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    for offset in (2,):
        day = calendar.nth_trading_day_after(d0, offset)
        previous_close = float(raw.loc[raw["date"] < day].sort_values("date").iloc[-1]["close"])
        raw.loc[raw["date"] == day, ["open", "high", "low", "close"]] = previous_close + 1
    write_ohlcv(raw, raw_path, "600000")

    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    result = BacktestService(tmp_path, runs).run(StrongGapUpStrategy(), d0, calendar.next_trading_day(d0))
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


def test_d3_delayed_entry_is_an_explicit_config_variant(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    raw_path = ohlcv_path(tmp_path, "600000", "raw")
    raw = pd.read_parquet(raw_path)
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    d2 = calendar.nth_trading_day_after(d0, 2)
    previous_close = float(raw.loc[raw["date"] < d2].sort_values("date").iloc[-1]["close"])
    raw.loc[raw["date"] == d2, ["open", "high", "low", "close"]] = previous_close + 1
    write_ohlcv(raw, raw_path, "600000")
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    strategy = StrongGapUpStrategy(StrongGapConfig(max_entry_wait_days=2))

    result = BacktestService(tmp_path, runs).run(strategy, d0, calendar.next_trading_day(d0))
    event = runs.backtest_events(result["run_id"], 10, 0)[0][0]

    assert event["entry_date"] == calendar.nth_trading_day_after(d0, 3).isoformat()
    assert event["entry_delay_trading_days"] == 2
    assert event["entry_kind"] == "execution_rollover"


def test_backtest_keeps_paired_d2_early_and_d3_continuation_events(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    d2 = calendar.nth_trading_day_after(d0, 2)

    result = BacktestService(tmp_path, runs).run(StrongGapUpStrategy(), d0, d2)
    events, total = runs.backtest_events(result["run_id"], 10, 0)

    assert total == 2
    assert {event["entry_kind"] for event in events} == {"early_entry", "continuation_entry"}
    assert len({event["comparison_pair_id"] for event in events}) == 1
    early = next(event for event in events if event["entry_kind"] == "early_entry")
    continuation = next(event for event in events if event["entry_kind"] == "continuation_entry")
    assert early["entry_date"] == d2.isoformat()
    assert continuation["entry_date"] == calendar.next_trading_day(d2).isoformat()
    assert continuation["d2_expansion_from_d0_close"] < 0.10
    assert result["metrics"]["pairs_with_early_and_continuation"] == 1
    assert result["metrics"]["entry_kind_metrics"]["continuation_entry"]["sample_size"] == 1


def test_four_day_time_exit_is_versioned_by_config(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    strategy = StrongGapUpStrategy(StrongGapConfig(max_holding_days=4))

    result = BacktestService(tmp_path, runs).run(strategy, d0, calendar.next_trading_day(d0))
    event = runs.backtest_events(result["run_id"], 10, 0)[0][0]

    assert event["exit_reason"] == "fixed_4d"
    assert event["exit_date"] == calendar.nth_trading_day_after(d0, 6).isoformat()


def test_gap_destroyed_at_d2_open_is_not_bought(tmp_path, qualifying_frames, d0):
    calendar = _install_fixture(tmp_path, qualifying_frames, d0)
    raw_path = ohlcv_path(tmp_path, "600000", "raw")
    raw = pd.read_parquet(raw_path)
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    d2 = calendar.nth_trading_day_after(d0, 2)
    floor = float(qualifying_frames[0].iloc[-2]["high"])
    raw.loc[raw["date"] == d2, ["open", "high", "low", "close"]] = [floor, floor + 0.1, floor - 0.1, floor]
    write_ohlcv(raw, raw_path, "600000")
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)

    result = BacktestService(tmp_path, runs).run(StrongGapUpStrategy(), d0, calendar.next_trading_day(d0))
    event = runs.backtest_events(result["run_id"], 10, 0)[0][0]

    assert event["status"] == "invalidated_before_entry"
    assert event["entry_date"] is None
    assert event["exit_reason"] == "gap_destroyed_at_entry_open"
