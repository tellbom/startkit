from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from stock_strategy_api.api.dependencies import registry, response_meta
from stock_strategy_api.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


@router.get("")
def list_strategies(request: Request, strategy_registry: StrategyRegistry = Depends(registry)) -> dict:
    data = [
        {
            **strategy.metadata().model_dump(mode="json"),
            "config": strategy.config_snapshot(),
            "config_schema": strategy.config_schema(),
            "config_hash": strategy.config_hash(),
        }
        for strategy in strategy_registry.list()
    ]
    return {"data": data, "meta": response_meta(request)}


@router.get("/{strategy_id}")
def strategy_detail(
    strategy_id: str,
    request: Request,
    strategy_registry: StrategyRegistry = Depends(registry),
) -> dict:
    strategy = strategy_registry.get(strategy_id)
    return {
        "data": {
            **strategy.metadata().model_dump(mode="json"),
            "config": strategy.config_snapshot(),
            "config_schema": strategy.config_schema(),
            "config_hash": strategy.config_hash(),
        },
        "meta": response_meta(request),
    }
