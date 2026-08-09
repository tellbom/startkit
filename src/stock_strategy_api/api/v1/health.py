from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from stock_strategy_api.core.clock import iso_now, now_shanghai
from stock_strategy_api.market_data.calendar import CalendarService

router = APIRouter(tags=["health"])


@router.get("/healthz")
def health() -> dict:
    return {"status": "ok", "generated_at": iso_now()}


@router.get("/readyz")
def ready(request: Request):
    latest_scan = request.app.state.runs.latest_successful_scan()
    checks = {
        "database": request.app.state.database.ping(),
        "strategies": bool(request.app.state.registry.list()),
        "calendar": False,
        "successful_scan": latest_scan is not None,
        "scan_is_current": False,
        "all_strategies_current": False,
    }
    expected_trade_date = None
    strategy_scan_dates: dict[str, str | None] = {}
    try:
        calendar = CalendarService(request.app.state.settings.data_dir)
        checks["calendar"] = bool(calendar.coverage())
        now = now_shanghai()
        if calendar.is_trading_day(now.date()) and now.time() >= dt.time(15, 30):
            expected_trade_date = now.date()
        else:
            expected_trade_date = calendar.prev_trading_day(now.date())
        checks["scan_is_current"] = bool(
            latest_scan and latest_scan["as_of_trade_date"] == expected_trade_date.isoformat()
        )
        for strategy in request.app.state.registry.list():
            strategy_id = strategy.metadata().strategy_id
            strategy_scan = request.app.state.runs.latest_successful_scan(strategy_id)
            strategy_scan_dates[strategy_id] = strategy_scan["as_of_trade_date"] if strategy_scan else None
        checks["all_strategies_current"] = bool(strategy_scan_dates) and all(
            value == expected_trade_date.isoformat() for value in strategy_scan_dates.values()
        )
    except Exception:
        checks["calendar"] = False
    is_ready = all(checks.values())
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
            "expected_trade_date": expected_trade_date.isoformat() if expected_trade_date else None,
            "latest_scan_date": latest_scan["as_of_trade_date"] if latest_scan else None,
            "strategy_scan_dates": strategy_scan_dates,
            "generated_at": iso_now(),
        },
    )
