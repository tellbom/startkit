from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from stock_strategy_api.api.dependencies import response_meta, runs
from stock_strategy_api.core.errors import ResourceNotFoundError
from stock_strategy_api.repositories.run_repository import RunRepository

router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])


@router.get("")
def list_backtests(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    repository: RunRepository = Depends(runs),
) -> dict:
    rows, total = repository.list_backtests(limit, offset)
    return {"data": rows, "meta": response_meta(request, total=total, limit=limit, offset=offset)}


@router.get("/{run_id}")
def backtest_detail(run_id: str, request: Request, repository: RunRepository = Depends(runs)) -> dict:
    row = repository.get_backtest(run_id)
    if not row:
        raise ResourceNotFoundError("backtest not found", details={"run_id": run_id})
    return {"data": row, "meta": response_meta(request)}


@router.get("/{run_id}/events")
def backtest_events(
    run_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    repository: RunRepository = Depends(runs),
) -> dict:
    if not repository.get_backtest(run_id):
        raise ResourceNotFoundError("backtest not found", details={"run_id": run_id})
    rows, total = repository.backtest_events(run_id, limit, offset)
    return {"data": rows, "meta": response_meta(request, total=total, limit=limit, offset=offset)}
