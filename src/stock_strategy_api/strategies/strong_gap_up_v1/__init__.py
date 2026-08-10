from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.market_data.security_master import EligibilityResult
from stock_strategy_api.strategies.base import DetectionResult, Strategy, StrategyMetadata, StrategySignal
from stock_strategy_api.strategies.strong_gap_up_v1.config import StrongGapConfig
from stock_strategy_api.strategies.strong_gap_up_v1.detector import detect_signal
from stock_strategy_api.strategies.strong_gap_up_v1.lifecycle import advance_signal
from stock_strategy_api.strategies.strong_gap_up_v1.manifest import METADATA


class StrongGapUpStrategy(Strategy):
    def __init__(self, config: StrongGapConfig | None = None) -> None:
        self.config = config or StrongGapConfig()

    def metadata(self) -> StrategyMetadata:
        return METADATA.model_copy(deep=True)

    def config_snapshot(self) -> dict[str, Any]:
        return self.config.model_dump(mode="json")

    def config_schema(self) -> dict[str, Any]:
        return StrongGapConfig.model_json_schema()

    def config_hash(self) -> str:
        return self.config.digest()

    def detect(
        self,
        raw_history: pd.DataFrame,
        qfq_history: pd.DataFrame,
        as_of: dt.date,
        eligibility: EligibilityResult,
        *,
        universe_mode: str,
        survivorship_bias: bool,
        calendar_source: str,
        expected_previous_trade_date: dt.date | None = None,
    ) -> DetectionResult:
        return detect_signal(
            raw_history,
            qfq_history,
            as_of,
            eligibility,
            self.config,
            universe_mode=universe_mode,
            survivorship_bias=survivorship_bias,
            calendar_source=calendar_source,
            expected_previous_trade_date=expected_previous_trade_date,
        )

    def advance(
        self,
        signal: StrategySignal,
        raw_bars: pd.DataFrame,
        calendar: CalendarService,
        as_of: dt.date,
    ) -> StrategySignal:
        return advance_signal(
            signal,
            raw_bars,
            calendar,
            as_of,
            self.config,
        )


__all__ = ["StrongGapUpStrategy", "StrongGapConfig"]
