from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_definitions (
    strategy_id TEXT NOT NULL,
    version TEXT NOT NULL,
    name TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (strategy_id, version, config_hash)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    as_of_trade_date TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT,
    data_last_updated_at TEXT,
    UNIQUE (strategy_id, strategy_version, config_hash, as_of_trade_date)
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    phase TEXT NOT NULL,
    rule_score REAL NOT NULL,
    confirmation_date TEXT,
    earliest_entry_date TEXT,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (strategy_id, strategy_version, config_hash, signal_date, symbol)
);

CREATE INDEX IF NOT EXISTS ix_signals_query
ON signals(strategy_id, state, signal_date DESC, rule_score DESC, symbol ASC);

CREATE TABLE IF NOT EXISTS signal_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    transition_date TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(signal_id, to_state, transition_date),
    FOREIGN KEY(signal_id) REFERENCES signals(signal_id)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL,
    universe_mode TEXT NOT NULL,
    survivorship_bias INTEGER NOT NULL,
    security_master_pit INTEGER NOT NULL,
    production_verified INTEGER NOT NULL DEFAULT 0,
    parameters_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS backtest_events (
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
    UNIQUE(run_id, symbol, signal_date),
    FOREIGN KEY(run_id) REFERENCES backtest_runs(run_id)
);

CREATE INDEX IF NOT EXISTS ix_backtest_events_run
ON backtest_events(run_id, signal_date, symbol);

CREATE TABLE IF NOT EXISTS backtest_metrics (
    run_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_json TEXT NOT NULL,
    PRIMARY KEY (run_id, metric_name),
    FOREIGN KEY(run_id) REFERENCES backtest_runs(run_id)
);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ping(self) -> bool:
        try:
            with self.connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False
