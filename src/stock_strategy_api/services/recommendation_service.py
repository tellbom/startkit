from __future__ import annotations

import datetime as dt

from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.strategies.base import StrategySignal
from stock_strategy_api.strategies.registry import StrategyRegistry


class RecommendationService:
    def __init__(self, signals: SignalRepository, runs: RunRepository, registry: StrategyRegistry) -> None:
        self.signals = signals
        self.runs = runs
        self.registry = registry

    def list(
        self,
        *,
        strategy_id: str | None = None,
        state: str | list[str] | tuple[str, ...] | None = ("entry_eligible", "continuation_entry"),
        phase: str | None = None,
        symbol: str | None = None,
        as_of: dt.date | None = None,
        include_exhaustion: bool = False,
        include_legacy_versions: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[StrategySignal], int, dict | None]:
        selected = (self.registry.get(strategy_id),) if strategy_id else self.registry.list()
        current_strategies = (
            None
            if include_legacy_versions
            else tuple((item.metadata().strategy_id, item.metadata().version, item.config_hash()) for item in selected)
        )
        signals, total = self.signals.list_signals(
            strategy_id=strategy_id,
            current_strategies=current_strategies,
            state=state,
            phase=phase,
            symbol=symbol,
            as_of=as_of,
            include_exhaustion=include_exhaustion,
            limit=limit,
            offset=offset,
        )
        scan_filters = {}
        if not include_legacy_versions and len(selected) == 1:
            metadata = selected[0].metadata()
            scan_filters = {
                "strategy_version": metadata.version,
                "config_hash": selected[0].config_hash(),
            }
        return signals, total, self.runs.latest_successful_scan(strategy_id, as_of, **scan_filters)
