from __future__ import annotations

import datetime as dt

from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.strategies.registry import get_registry


def test_strategy_action_can_only_be_claimed_once(tmp_path):
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    strategy = get_registry().get("strong_gap_up_v1")
    as_of = dt.date(2026, 8, 11)

    assert runs.claim_strategy_action(strategy, as_of, "daily_result", "hash-1") is True
    assert runs.claim_strategy_action(strategy, as_of, "daily_result", "hash-1") is False
    assert runs.strategy_action(strategy, as_of, "daily_result")["status"] == "running"


def test_completed_strategy_action_stays_idempotent(tmp_path):
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    strategy = get_registry().get("strong_gap_up_v1")
    as_of = dt.date(2026, 8, 11)

    assert runs.claim_strategy_action(strategy, as_of, "daily_result", "hash-1") is True
    runs.finish_strategy_action(strategy, as_of, "daily_result", "hash-1", {"errcode": 0})

    action = runs.strategy_action(strategy, as_of, "daily_result")
    assert action["status"] == "success"
    assert action["result"] == {"errcode": 0}
    assert runs.claim_strategy_action(strategy, as_of, "daily_result", "hash-2") is False
