from __future__ import annotations

import datetime as dt

from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.strategies.base import StrategySignal


class RecommendationService:
    def __init__(self, signals: SignalRepository, runs: RunRepository) -> None:
        self.signals = signals
        self.runs = runs

    def list(
        self,
        *,
        strategy_id: str | None = None,
        state: str | None = "confirmed",
        phase: str | None = None,
        symbol: str | None = None,
        as_of: dt.date | None = None,
        include_exhaustion: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[StrategySignal], int, dict | None]:
        signals, total = self.signals.list_signals(
            strategy_id=strategy_id,
            state=state,
            phase=phase,
            symbol=symbol,
            as_of=as_of,
            include_exhaustion=include_exhaustion,
            limit=limit,
            offset=offset,
        )
        return signals, total, self.runs.latest_successful_scan(strategy_id, as_of)
