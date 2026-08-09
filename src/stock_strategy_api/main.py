from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from stock_strategy_api.api.errors import (
    domain_error_handler,
    internal_error_handler,
    request_validation_handler,
    value_error_handler,
)
from stock_strategy_api.api.v1 import backtests, health, recommendations, stocks, strategies
from stock_strategy_api.core.config import Settings, get_settings
from stock_strategy_api.core.errors import DomainError
from stock_strategy_api.core.logging import configure_logging
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.services.recommendation_service import RecommendationService
from stock_strategy_api.strategies.registry import get_registry


def create_app(settings: Settings | None = None) -> FastAPI:
    application_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_logging()
        application_settings.ensure_runtime_dirs()
        database = Database(application_settings.database_path)
        database.initialize()
        signal_repository = SignalRepository(database)
        run_repository = RunRepository(database)
        strategy_registry = get_registry()
        for strategy in strategy_registry.list():
            run_repository.register_strategy(strategy)
        application.state.settings = application_settings
        application.state.database = database
        application.state.signals = signal_repository
        application.state.runs = run_repository
        application.state.registry = strategy_registry
        application.state.recommendations = RecommendationService(signal_repository, run_repository)
        yield

    application = FastAPI(
        title="Stock Strategy API",
        version="0.1.0",
        description="Rule-based CSI 300 recommendation and backtest API.",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    application.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(ValueError, value_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, request_validation_handler)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, internal_error_handler)
    application.include_router(health.router)
    application.include_router(strategies.router)
    application.include_router(recommendations.router)
    application.include_router(stocks.router)
    application.include_router(backtests.router)
    return application


app = create_app()
