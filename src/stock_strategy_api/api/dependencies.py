from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import Request

from stock_strategy_api.core.clock import iso_now, now_shanghai
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.services.recommendation_service import RecommendationService
from stock_strategy_api.strategies.registry import StrategyRegistry


def registry(request: Request) -> StrategyRegistry:
    return request.app.state.registry


def runs(request: Request) -> RunRepository:
    return request.app.state.runs


def signals(request: Request) -> SignalRepository:
    return request.app.state.signals


def recommendations(request: Request) -> RecommendationService:
    return request.app.state.recommendations


def response_meta(
    request: Request,
    *,
    as_of: str | None = None,
    data_last_updated_at: str | None = None,
    stale: bool = False,
    warnings: list[str] | None = None,
    total: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "request_id": getattr(request.state, "request_id", None),
        "generated_at": iso_now(),
        "as_of_trade_date": as_of,
        "data_last_updated_at": data_last_updated_at,
        "stale": stale,
        "warnings": warnings or [],
    }
    if total is not None:
        meta.update({"total": total, "limit": limit, "offset": offset})
    return meta


def data_is_stale(request: Request, timestamp: str | None) -> bool:
    if not timestamp:
        return True
    try:
        updated = dt.datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if updated.tzinfo is None:
        return True
    age = now_shanghai() - updated
    return age > dt.timedelta(hours=request.app.state.settings.data_freshness_hours)
