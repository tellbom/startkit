from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from stock_strategy_api.core.clock import iso_now
from stock_strategy_api.core.errors import RunConflictError
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.strategies.base import Strategy, StrategySignal


class RunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def register_strategy(self, strategy: Strategy) -> None:
        metadata = strategy.metadata()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_definitions
                (strategy_id, version, name, metadata_json, config_json, config_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, version, config_hash)
                DO UPDATE SET metadata_json=excluded.metadata_json, config_json=excluded.config_json,
                    name=excluded.name, updated_at=excluded.updated_at
                """,
                (
                    metadata.strategy_id,
                    metadata.version,
                    metadata.name,
                    metadata.model_dump_json(),
                    json.dumps(strategy.config_snapshot(), ensure_ascii=False, sort_keys=True),
                    strategy.config_hash(),
                    iso_now(),
                ),
            )

    def start_scan(self, strategy: Strategy, as_of: dt.date) -> str:
        metadata = strategy.metadata()
        natural_key = (metadata.strategy_id, metadata.version, strategy.config_hash(), as_of.isoformat())
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT run_id, status FROM scan_runs
                WHERE strategy_id=? AND strategy_version=? AND config_hash=? AND as_of_trade_date=?""",
                natural_key,
            ).fetchone()
            if existing and existing["status"] == "running":
                raise RunConflictError(
                    "scan is already running",
                    details={"run_id": existing["run_id"]},
                )
            run_id = existing["run_id"] if existing else uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO scan_runs
                (run_id, strategy_id, strategy_version, config_hash, as_of_trade_date, status, started_at, stats_json)
                VALUES (?, ?, ?, ?, ?, 'running', ?, '{}')
                ON CONFLICT(strategy_id, strategy_version, config_hash, as_of_trade_date)
                DO UPDATE SET status='running', started_at=excluded.started_at, finished_at=NULL,
                    stats_json='{}', error_json=NULL
                """,
                (run_id, *natural_key, iso_now()),
            )
        return str(run_id)

    def finish_scan(
        self,
        run_id: str,
        *,
        status: str,
        stats: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        data_last_updated_at: str | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE scan_runs SET status=?, finished_at=?, stats_json=?, error_json=?, data_last_updated_at=?
                WHERE run_id=?
                """,
                (
                    status,
                    iso_now(),
                    json.dumps(stats or {}, ensure_ascii=False),
                    json.dumps(error, ensure_ascii=False) if error else None,
                    data_last_updated_at,
                    run_id,
                ),
            )

    def commit_scan_results(
        self,
        run_id: str,
        signals: list[StrategySignal],
        *,
        transition_date: dt.date,
        stats: dict[str, Any],
        data_last_updated_at: str,
    ) -> None:
        with self.database.connect() as connection:
            for signal in signals:
                SignalRepository._upsert(connection, signal, transition_date=transition_date)
            connection.execute(
                """UPDATE scan_runs SET status='success', finished_at=?, stats_json=?, error_json=NULL,
                data_last_updated_at=? WHERE run_id=?""",
                (
                    iso_now(),
                    json.dumps(stats, ensure_ascii=False),
                    data_last_updated_at,
                    run_id,
                ),
            )

    def latest_successful_scan(
        self, strategy_id: str | None = None, as_of: dt.date | None = None
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM scan_runs WHERE status='success'"
        conditions: list[str] = []
        parameters: list[str] = []
        if strategy_id:
            conditions.append("strategy_id=?")
            parameters.append(strategy_id)
        if as_of:
            conditions.append("as_of_trade_date<=?")
            parameters.append(as_of.isoformat())
        if conditions:
            query += " AND " + " AND ".join(conditions)
        query += " ORDER BY as_of_trade_date DESC LIMIT 1"
        with self.database.connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return _row_to_dict(row) if row else None

    def get_scan(self, run_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM scan_runs WHERE run_id=?", (run_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def successful_scan_for(self, strategy: Strategy, as_of: dt.date) -> dict[str, Any] | None:
        metadata = strategy.metadata()
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM scan_runs
                WHERE strategy_id=? AND strategy_version=? AND config_hash=?
                  AND as_of_trade_date=? AND status='success'""",
                (metadata.strategy_id, metadata.version, strategy.config_hash(), as_of.isoformat()),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def create_backtest(
        self,
        strategy: Strategy,
        start: dt.date,
        end: dt.date,
        *,
        universe_mode: str,
        survivorship_bias: bool,
        security_master_pit: bool,
        parameters: dict[str, Any],
    ) -> str:
        run_id = uuid.uuid4().hex
        metadata = strategy.metadata()
        run_config_hash = hashlib.sha256(
            json.dumps(
                {
                    "strategy_id": metadata.strategy_id,
                    "strategy_version": metadata.version,
                    "strategy_config_hash": strategy.config_hash(),
                    "parameters": parameters,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:16]
        production_verified = universe_mode == "point_in_time" and not survivorship_bias and security_master_pit
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO backtest_runs
                (run_id, strategy_id, strategy_version, config_hash, start_date, end_date, status,
                 universe_mode, survivorship_bias, security_master_pit, production_verified,
                 parameters_json, started_at)
                VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    metadata.strategy_id,
                    metadata.version,
                    run_config_hash,
                    start.isoformat(),
                    end.isoformat(),
                    universe_mode,
                    int(survivorship_bias),
                    int(security_master_pit),
                    int(production_verified),
                    json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                    iso_now(),
                ),
            )
        return run_id

    def add_backtest_event(self, run_id: str, event: dict[str, Any]) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO backtest_events
                (run_id, symbol, signal_date, phase, entry_date, exit_date, status,
                 gross_return, net_return, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, symbol, signal_date)
                DO UPDATE SET phase=excluded.phase, entry_date=excluded.entry_date,
                    exit_date=excluded.exit_date, status=excluded.status,
                    gross_return=excluded.gross_return, net_return=excluded.net_return,
                    payload_json=excluded.payload_json
                """,
                (
                    run_id,
                    event["symbol"],
                    event["signal_date"],
                    event["phase"],
                    event.get("entry_date"),
                    event.get("exit_date"),
                    event["status"],
                    event.get("gross_return"),
                    event.get("net_return"),
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                ),
            )

    def finish_backtest(self, run_id: str, status: str, metrics: dict, error: dict | None = None) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE backtest_runs SET status=?, metrics_json=?, error_json=?, finished_at=?,
                security_master_pit=COALESCE(?, security_master_pit),
                production_verified=COALESCE(?, production_verified)
                WHERE run_id=?""",
                (
                    status,
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                    json.dumps(error, ensure_ascii=False) if error else None,
                    iso_now(),
                    int(metrics["security_master_pit"]) if "security_master_pit" in metrics else None,
                    int(metrics["production_verified"]) if "production_verified" in metrics else None,
                    run_id,
                ),
            )
            connection.execute("DELETE FROM backtest_metrics WHERE run_id=?", (run_id,))
            connection.executemany(
                "INSERT INTO backtest_metrics (run_id, metric_name, metric_json) VALUES (?, ?, ?)",
                [
                    (run_id, name, json.dumps(value, ensure_ascii=False, sort_keys=True))
                    for name, value in sorted(metrics.items())
                ],
            )

    def list_backtests(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        with self.database.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) count FROM backtest_runs").fetchone()["count"])
            rows = connection.execute(
                "SELECT * FROM backtest_runs ORDER BY started_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [_row_to_dict(row) for row in rows], total

    def get_backtest(self, run_id: str) -> dict | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def backtest_events(self, run_id: str, limit: int, offset: int) -> tuple[list[dict], int]:
        with self.database.connect() as connection:
            total = int(
                connection.execute("SELECT COUNT(*) count FROM backtest_events WHERE run_id=?", (run_id,)).fetchone()[
                    "count"
                ]
            )
            rows = connection.execute(
                """SELECT payload_json FROM backtest_events WHERE run_id=?
                ORDER BY signal_date, symbol LIMIT ? OFFSET ?""",
                (run_id, limit, offset),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows], total


def _row_to_dict(row) -> dict:
    result = dict(row)
    for key in ("stats_json", "error_json", "parameters_json", "metrics_json"):
        if key in result:
            result[key.removesuffix("_json")] = json.loads(result.pop(key) or "null")
    for key in ("survivorship_bias", "security_master_pit", "production_verified"):
        if key in result:
            result[key] = bool(result[key])
    return result
