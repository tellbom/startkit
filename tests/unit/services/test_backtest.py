from __future__ import annotations

from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.services.backtest_service import BacktestService, CostConfig
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy


def test_costs_reduce_return(tmp_path):
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    service = BacktestService(tmp_path, RunRepository(database), costs=CostConfig(3, 5, 5, 5))
    gross = 110 / 100 - 1
    net = service._net_return(100, 110)
    assert net < gross
    assert net > 0


def test_cost_configuration_changes_backtest_run_hash(tmp_path, d0):
    database = Database(tmp_path / "strategy.sqlite3")
    database.initialize()
    runs = RunRepository(database)
    strategy = StrongGapUpStrategy()
    first = runs.create_backtest(
        strategy,
        d0,
        d0,
        universe_mode="point_in_time",
        survivorship_bias=False,
        security_master_pit=True,
        parameters={"costs": CostConfig(stamp_duty_bps=5).to_dict()},
    )
    second = runs.create_backtest(
        strategy,
        d0,
        d0,
        universe_mode="point_in_time",
        survivorship_bias=False,
        security_master_pit=True,
        parameters={"costs": CostConfig(stamp_duty_bps=10).to_dict()},
    )
    assert runs.get_backtest(first)["config_hash"] != runs.get_backtest(second)["config_hash"]
