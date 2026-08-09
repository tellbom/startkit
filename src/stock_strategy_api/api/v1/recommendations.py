from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from stock_strategy_api.api.dependencies import data_is_stale, recommendations, registry, response_meta
from stock_strategy_api.core.errors import InvalidDateError
from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.services.recommendation_service import RecommendationService
from stock_strategy_api.strategies.base import GapPhase, SignalState
from stock_strategy_api.strategies.registry import StrategyRegistry

router = APIRouter(tags=["recommendations"])


@router.get("/api/v1/strategies/{strategy_id}/recommendations")
def strategy_recommendations(
    strategy_id: str,
    request: Request,
    state: SignalState | Literal["all"] = SignalState.CONFIRMED,
    phase: GapPhase | None = None,
    risk: Literal["exclude_exhaustion", "include_exhaustion"] = "exclude_exhaustion",
    as_of: dt.date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: RecommendationService = Depends(recommendations),
    strategy_registry: StrategyRegistry = Depends(registry),
) -> dict:
    strategy_registry.get(strategy_id)
    return _list(
        request,
        service,
        strategy_id=strategy_id,
        state=None if state == "all" else state.value,
        phase=phase.value if phase else None,
        as_of=as_of,
        include_exhaustion=risk == "include_exhaustion",
        limit=limit,
        offset=offset,
    )


@router.get("/api/v1/recommendations")
def all_recommendations(
    request: Request,
    strategy_id: str | None = None,
    state: SignalState | Literal["all"] = SignalState.CONFIRMED,
    phase: GapPhase | None = None,
    risk: Literal["exclude_exhaustion", "include_exhaustion"] = "exclude_exhaustion",
    as_of: dt.date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: RecommendationService = Depends(recommendations),
    strategy_registry: StrategyRegistry = Depends(registry),
) -> dict:
    if strategy_id:
        strategy_registry.get(strategy_id)
    return _list(
        request,
        service,
        strategy_id=strategy_id,
        state=None if state == "all" else state.value,
        phase=phase.value if phase else None,
        as_of=as_of,
        include_exhaustion=risk == "include_exhaustion",
        limit=limit,
        offset=offset,
    )


def _list(
    request: Request,
    service: RecommendationService,
    **filters,
) -> dict:
    requested_as_of = filters["as_of"]
    if requested_as_of:
        calendar = CalendarService(request.app.state.settings.data_dir)
        if not calendar.is_trading_day(requested_as_of):
            raise InvalidDateError(f"{requested_as_of} is not a trading day")
    rows, total, latest = service.list(**filters)
    latest_date = latest["as_of_trade_date"] if latest else None
    warnings: list[str] = []
    if any(row.survivorship_bias for row in rows):
        warnings.append("Results use a current CSI 300 constituent snapshot and may have survivorship bias.")
    if any(not row.security_master_pit for row in rows):
        warnings.append("Historical eligibility used a non-PIT security master snapshot.")
    if any("calendar_fallback_source" in row.risk_flags for row in rows):
        warnings.append("Results use the offline exchange calendar fallback; refresh before production use.")
    updated_at = latest.get("data_last_updated_at") if latest else None
    stale = data_is_stale(request, updated_at)
    if stale:
        warnings.append("No sufficiently fresh successful scan is available for this query.")
    response_as_of = requested_as_of.isoformat() if requested_as_of else latest_date
    return {
        "data": [{**row.model_dump(mode="json"), "as_of_trade_date": response_as_of} for row in rows],
        "meta": response_meta(
            request,
            as_of=response_as_of,
            data_last_updated_at=updated_at,
            stale=stale,
            warnings=warnings,
            total=total,
            limit=filters["limit"],
            offset=filters["offset"],
        ),
    }
