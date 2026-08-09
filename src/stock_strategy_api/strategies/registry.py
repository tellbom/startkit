from __future__ import annotations

from stock_strategy_api.core.errors import UnknownStrategyError
from stock_strategy_api.strategies.base import Strategy


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        strategy_id = strategy.metadata().strategy_id
        if strategy_id in self._strategies:
            raise ValueError(f"strategy {strategy_id!r} is already registered")
        self._strategies[strategy_id] = strategy

    def get(self, strategy_id: str) -> Strategy:
        try:
            return self._strategies[strategy_id]
        except KeyError as exc:
            raise UnknownStrategyError(f"unknown strategy: {strategy_id}") from exc

    def list(self) -> tuple[Strategy, ...]:
        return tuple(self._strategies[key] for key in sorted(self._strategies))


_registry: StrategyRegistry | None = None


def get_registry() -> StrategyRegistry:
    global _registry
    if _registry is None:
        from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy

        _registry = StrategyRegistry()
        _registry.register(StrongGapUpStrategy())
    return _registry
