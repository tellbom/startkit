from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from stock_strategy_api.core.errors import RunConflictError
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.strategies.base import SignalState
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapConfig, StrongGapUpStrategy


def _signal(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    result = StrongGapUpStrategy().detect(
        raw, qfq, d0, eligible, universe_mode="point_in_time", survivorship_bias=False, calendar_source="fixture"
    )
    assert result.signal
    return result.signal


def test_legacy_backtest_event_table_migrates_to_entry_kind_key(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE backtest_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_date TEXT NOT NULL,
                phase TEXT NOT NULL,
                entry_date TEXT,
                exit_date TEXT,
                status TEXT NOT NULL,
                gross_return REAL,
                net_return REAL,
                payload_json TEXT NOT NULL,
                UNIQUE(run_id, symbol, signal_date)
            )"""
        )

    database = Database(path)
    database.initialize()

    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(backtest_events)")}
        unique_indexes = [
            row["name"] for row in connection.execute("PRAGMA index_list(backtest_events)") if row["unique"]
        ]
        indexed_columns = [
            [item["name"] for item in connection.execute(f"PRAGMA index_info('{index}')")] for index in unique_indexes
        ]
    assert "entry_kind" in columns
    assert ["run_id", "symbol", "signal_date", "entry_kind"] in indexed_columns


def test_signal_upsert_is_idempotent_and_audited(tmp_path, qualifying_frames, d0, eligible):
    database = Database(tmp_path / "result.sqlite3")
    database.initialize()
    repository = SignalRepository(database)
    signal = _signal(qualifying_frames, d0, eligible)
    first = repository.upsert(signal)
    second = repository.upsert(signal)
    assert first == second
    rows, total = repository.list_signals(state="triggered", include_exhaustion=True)
    assert total == 1
    assert rows[0].symbol == "600000"
    with database.connect() as connection:
        transitions = connection.execute("SELECT COUNT(*) count FROM signal_transitions").fetchone()["count"]
    assert transitions == 1


def test_batch_upsert_rolls_back_on_failure(tmp_path, qualifying_frames, d0, eligible):
    database = Database(tmp_path / "result.sqlite3")
    database.initialize()
    repository = SignalRepository(database)
    signal = _signal(qualifying_frames, d0, eligible)
    with pytest.raises(AttributeError):
        repository.upsert_many([signal, None])  # type: ignore[list-item]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) count FROM signals").fetchone()["count"] == 0


def test_new_config_hash_never_overwrites_old_signal(tmp_path, qualifying_frames, d0, eligible):
    database = Database(tmp_path / "result.sqlite3")
    database.initialize()
    repository = SignalRepository(database)
    original = _signal(qualifying_frames, d0, eligible)
    raw, qfq = qualifying_frames
    changed = (
        StrongGapUpStrategy(StrongGapConfig(score_gap_cap=0.02))
        .detect(
            raw,
            qfq,
            d0,
            eligible,
            universe_mode="point_in_time",
            survivorship_bias=False,
            calendar_source="fixture",
        )
        .signal
    )
    assert changed is not None
    assert changed.config_hash != original.config_hash
    assert changed.rule_score != original.rule_score
    repository.upsert_many([original, changed])
    assert repository.list_signals(include_exhaustion=True)[1] == 2


def test_active_signals_are_isolated_by_version_and_config(tmp_path, qualifying_frames, d0, eligible):
    database = Database(tmp_path / "result.sqlite3")
    database.initialize()
    repository = SignalRepository(database)
    current = _signal(qualifying_frames, d0, eligible)
    legacy = current.model_copy(update={"strategy_version": "1.0.0", "config_hash": "legacy-config"})
    repository.upsert_many([current, legacy])

    active = repository.active(
        current.strategy_id,
        strategy_version=current.strategy_version,
        config_hash=current.config_hash,
    )

    assert len(active) == 1
    assert active[0][1].strategy_version == "2.1.0"


def test_signal_pagination_has_stable_non_overlapping_pages(tmp_path, qualifying_frames, d0, eligible):
    database = Database(tmp_path / "result.sqlite3")
    database.initialize()
    repository = SignalRepository(database)
    base = _signal(qualifying_frames, d0, eligible)
    repository.upsert_many(
        [
            base.model_copy(update={"symbol": symbol, "rule_score": score})
            for symbol, score in (("600001", 80.0), ("600002", 80.0), ("600003", 70.0))
        ]
    )
    first, total = repository.list_signals(include_exhaustion=True, limit=2, offset=0)
    second, _ = repository.list_signals(include_exhaustion=True, limit=2, offset=2)
    assert total == 3
    assert [row.symbol for row in first] == ["600001", "600002"]
    assert [row.symbol for row in second] == ["600003"]


def test_as_of_query_reconstructs_historical_lifecycle_state(tmp_path, qualifying_frames, d0, eligible):
    database = Database(tmp_path / "result.sqlite3")
    database.initialize()
    repository = SignalRepository(database)
    signal = _signal(qualifying_frames, d0, eligible)
    repository.upsert(signal, transition_date=d0)
    confirmed = signal.model_copy(
        update={"state": SignalState.CONFIRMED, "confirmation_date": d0 + dt.timedelta(days=3)}
    )
    repository.upsert(confirmed, transition_date=d0 + dt.timedelta(days=3))

    historical, total = repository.list_signals(
        state="triggered", as_of=d0 + dt.timedelta(days=1), include_exhaustion=True
    )
    assert total == 1
    assert historical[0].state == "triggered"
    assert repository.list_signals(state="confirmed", as_of=d0 + dt.timedelta(days=1), include_exhaustion=True)[1] == 0


def test_scan_natural_key_prevents_concurrent_duplicate_run(tmp_path, d0):
    database = Database(tmp_path / "result.sqlite3")
    database.initialize()
    repository = RunRepository(database)
    strategy = StrongGapUpStrategy()
    repository.start_scan(strategy, d0)
    with pytest.raises(RunConflictError):
        repository.start_scan(strategy, d0)
