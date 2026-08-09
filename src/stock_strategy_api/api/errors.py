from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from stock_strategy_api.core.errors import DomainError

LOGGER = logging.getLogger(__name__)


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    status_code = {
        "unknown_strategy": 404,
        "resource_not_found": 404,
        "data_unavailable": 503,
        "run_conflict": 409,
    }.get(exc.code, 422)
    return JSONResponse(
        status_code=status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "invalid_request",
            "message": str(exc),
            "details": {},
            "request_id": getattr(request.state, "request_id", None),
        },
    )


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "request validation failed",
            "details": {"errors": exc.errors()},
            "request_id": getattr(request.state, "request_id", None),
        },
    )


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    LOGGER.exception("unhandled request error", extra={"request_id": request_id})
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "internal service error",
            "details": {},
            "request_id": request_id,
        },
    )
