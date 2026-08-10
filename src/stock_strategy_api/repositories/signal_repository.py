from __future__ import annotations

import datetime as dt
import sqlite3

from stock_strategy_api.core.clock import iso_now
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.strategies.base import StrategySignal


class SignalRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert(self, signal: StrategySignal, *, transition_date: dt.date | None = None) -> int:
        with self.database.connect() as connection:
            return self._upsert(connection, signal, transition_date=transition_date)

    def upsert_many(self, signals: list[StrategySignal], *, transition_date: dt.date | None = None) -> list[int]:
        with self.database.connect() as connection:
            return [self._upsert(connection, signal, transition_date=transition_date) for signal in signals]

    @staticmethod
    def _upsert(
        connection: sqlite3.Connection,
        signal: StrategySignal,
        *,
        transition_date: dt.date | None,
    ) -> int:
        payload = signal.model_dump_json()
        now = iso_now()
        existing = connection.execute(
            """
                SELECT signal_id, state, payload_json FROM signals
                WHERE strategy_id=? AND strategy_version=? AND config_hash=? AND signal_date=? AND symbol=?
                """,
            (
                signal.strategy_id,
                signal.strategy_version,
                signal.config_hash,
                signal.signal_date.isoformat(),
                signal.symbol,
            ),
        ).fetchone()
        connection.execute(
            """
                INSERT INTO signals (
                    strategy_id, strategy_version, config_hash, signal_date, symbol, state, phase,
                    rule_score, confirmation_date, earliest_entry_date, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, strategy_version, config_hash, signal_date, symbol)
                DO UPDATE SET state=excluded.state, phase=excluded.phase, rule_score=excluded.rule_score,
                    confirmation_date=excluded.confirmation_date,
                    earliest_entry_date=excluded.earliest_entry_date,
                    payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
            (
                signal.strategy_id,
                signal.strategy_version,
                signal.config_hash,
                signal.signal_date.isoformat(),
                signal.symbol,
                signal.state.value,
                signal.phase.value,
                signal.rule_score,
                _date(signal.confirmation_date),
                _date(signal.earliest_entry_date),
                payload,
                now,
            ),
        )
        row = connection.execute(
            """
                SELECT signal_id FROM signals
                WHERE strategy_id=? AND strategy_version=? AND config_hash=? AND signal_date=? AND symbol=?
                """,
            (
                signal.strategy_id,
                signal.strategy_version,
                signal.config_hash,
                signal.signal_date.isoformat(),
                signal.symbol,
            ),
        ).fetchone()
        signal_id = int(row["signal_id"])
        previous_state = existing["state"] if existing else None
        if previous_state != signal.state.value or (existing and existing["payload_json"] != payload):
            connection.execute(
                """
                    INSERT INTO signal_transitions
                    (signal_id, from_state, to_state, transition_date, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(signal_id, to_state, transition_date)
                    DO UPDATE SET payload_json=excluded.payload_json, created_at=excluded.created_at
                    """,
                (
                    signal_id,
                    previous_state,
                    signal.state.value,
                    (transition_date or signal.signal_date).isoformat(),
                    payload,
                    now,
                ),
            )
        return signal_id

    def get(self, signal_id: int) -> StrategySignal | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT payload_json FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
        return StrategySignal.model_validate_json(row["payload_json"]) if row else None

    def list_signals(
        self,
        *,
        strategy_id: str | None = None,
        state: str | None = None,
        phase: str | None = None,
        symbol: str | None = None,
        as_of: dt.date | None = None,
        include_exhaustion: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[StrategySignal], int]:
        if as_of:
            return self._list_signals_as_of(
                strategy_id=strategy_id,
                state=state,
                phase=phase,
                symbol=symbol,
                as_of=as_of,
                include_exhaustion=include_exhaustion,
                limit=limit,
                offset=offset,
            )
        clauses, parameters = [], []
        if strategy_id:
            clauses.append("strategy_id=?")
            parameters.append(strategy_id)
        if state:
            clauses.append("state=?")
            parameters.append(state)
        if phase:
            clauses.append("phase=?")
            parameters.append(phase)
        if symbol:
            clauses.append("symbol=?")
            parameters.append(symbol)
        if not include_exhaustion:
            clauses.append("phase<>'exhaustion_risk'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connect() as connection:
            total = int(
                connection.execute(f"SELECT COUNT(*) count FROM signals {where}", parameters).fetchone()["count"]
            )
            rows = connection.execute(
                f"""
                SELECT payload_json FROM signals {where}
                ORDER BY signal_date DESC, rule_score DESC, symbol ASC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        return [StrategySignal.model_validate_json(row["payload_json"]) for row in rows], total

    def _list_signals_as_of(
        self,
        *,
        strategy_id: str | None,
        state: str | None,
        phase: str | None,
        symbol: str | None,
        as_of: dt.date,
        include_exhaustion: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[StrategySignal], int]:
        clauses = ["s.signal_date<=?"]
        parameters: list[str] = [as_of.isoformat()]
        if strategy_id:
            clauses.append("s.strategy_id=?")
            parameters.append(strategy_id)
        if state:
            clauses.append("t.to_state=?")
            parameters.append(state)
        if phase:
            clauses.append("s.phase=?")
            parameters.append(phase)
        if symbol:
            clauses.append("s.symbol=?")
            parameters.append(symbol)
        if not include_exhaustion:
            clauses.append("s.phase<>'exhaustion_risk'")
        where = " AND ".join(clauses)
        join = """JOIN signal_transitions t ON t.transition_id=(
            SELECT transition_id FROM signal_transitions history
            WHERE history.signal_id=s.signal_id AND history.transition_date<=?
            ORDER BY history.transition_date DESC, history.transition_id DESC LIMIT 1
        )"""
        query_parameters = [as_of.isoformat(), *parameters]
        with self.database.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) count FROM signals s {join} WHERE {where}", query_parameters
                ).fetchone()["count"]
            )
            rows = connection.execute(
                f"""SELECT t.payload_json FROM signals s {join} WHERE {where}
                ORDER BY s.signal_date DESC, s.rule_score DESC, s.symbol ASC
                LIMIT ? OFFSET ?""",
                [*query_parameters, limit, offset],
            ).fetchall()
        return [StrategySignal.model_validate_json(row["payload_json"]) for row in rows], total

    def active(
        self,
        strategy_id: str,
        *,
        strategy_version: str | None = None,
        config_hash: str | None = None,
    ) -> list[tuple[int, StrategySignal]]:
        terminal = ("invalidated", "indeterminate", "expired", "weak_d1")
        placeholders = ",".join("?" for _ in terminal)
        clauses = ["strategy_id=?", f"state NOT IN ({placeholders})"]
        parameters: list[str] = [strategy_id, *terminal]
        if strategy_version:
            clauses.append("strategy_version=?")
            parameters.append(strategy_version)
        if config_hash:
            clauses.append("config_hash=?")
            parameters.append(config_hash)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT signal_id, payload_json FROM signals WHERE {' AND '.join(clauses)}",
                parameters,
            ).fetchall()
        return [(int(row["signal_id"]), StrategySignal.model_validate_json(row["payload_json"])) for row in rows]

    def latest_signal_date(self, strategy_id: str | None = None) -> dt.date | None:
        query = "SELECT MAX(signal_date) value FROM signals"
        params: tuple = ()
        if strategy_id:
            query += " WHERE strategy_id=?"
            params = (strategy_id,)
        with self.database.connect() as connection:
            value = connection.execute(query, params).fetchone()["value"]
        return dt.date.fromisoformat(value) if value else None


def _date(value: dt.date | None) -> str | None:
    return value.isoformat() if value else None
