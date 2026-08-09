from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from stock_strategy_api.api.dependencies import data_is_stale, recommendations, response_meta
from stock_strategy_api.market_data.symbols import is_shanghai_or_shenzhen, normalize_symbol
from stock_strategy_api.services.recommendation_service import RecommendationService

router = APIRouter(prefix="/api/v1/stocks", tags=["stocks"])


@router.get("/{symbol}/recommendations")
def stock_recommendations(
    symbol: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: RecommendationService = Depends(recommendations),
) -> dict:
    normalized = normalize_symbol(symbol)
    if not is_shanghai_or_shenzhen(normalized):
        raise ValueError("stock symbol must belong to Shanghai or Shenzhen")
    rows, total, latest = service.list(
        symbol=normalized,
        state=None,
        include_exhaustion=True,
        limit=limit,
        offset=offset,
    )
    as_of = latest["as_of_trade_date"] if latest else None
    return {
        "data": [{**row.model_dump(mode="json"), "as_of_trade_date": as_of} for row in rows],
        "meta": response_meta(
            request,
            as_of=as_of,
            data_last_updated_at=latest.get("data_last_updated_at") if latest else None,
            stale=data_is_stale(request, latest.get("data_last_updated_at") if latest else None),
            total=total,
            limit=limit,
            offset=offset,
        ),
    }
