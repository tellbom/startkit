from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.market_data.security_master import EligibilityResult


class SignalState(StrEnum):
    TRIGGERED = "triggered"
    WATCHING_D1 = "watching_d1"
    WATCHING_D2 = "watching_d2"
    PARTIALLY_FILLED = "partially_filled"
    WEAK_D1 = "weak_d1"
    ENTRY_ELIGIBLE = "entry_eligible"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    INDETERMINATE = "indeterminate"
    EXPIRED = "expired"


class GapPhase(StrEnum):
    PERSISTENT = "persistent_candidate"
    ACCELERATING = "accelerating_candidate"
    EXHAUSTION = "exhaustion_risk"


class D1Confirmation(StrEnum):
    FULLY_UNFILLED = "fully_unfilled"
    PARTIAL_RECLAIMED = "partial_reclaimed"
    PARTIAL_WEAK = "partial_weak"
    FULLY_FILLED = "fully_filled"
    INDETERMINATE = "indeterminate"


class RuleCheck(BaseModel):
    name: str
    passed: bool
    actual: float | int | str | None = None
    operator: str
    threshold: float | int | str | None = None


class StrategyMetadata(BaseModel):
    strategy_id: str
    version: str
    name: str
    description: str
    market: str = "CSI300"
    recommendation_kind: str = "rule_based_observation"
    risk_disclosure: str


class StrategySignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    strategy_version: str
    config_hash: str
    symbol: str
    stock_name: str
    signal_date: dt.date
    confirmation_date: dt.date | None = None
    earliest_entry_date: dt.date | None = None
    state: SignalState = SignalState.TRIGGERED
    phase: GapPhase = GapPhase.PERSISTENT
    eligible: bool = True
    exclusion_reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    gap_floor: float
    gap_ceiling: float
    gap_top: float | None = None
    gap_pct: float
    remaining_gap_pct: float = 1.0
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    volume_ratio: float
    close_location: float
    rise_return: float
    platform_amplitude: float
    platform_drift: float
    rule_score: float
    d0_score: float | None = None
    d1_score: float | None = None
    score_components: dict[str, float]
    rule_checks: list[RuleCheck]
    reasons: list[str]
    invalidated_date: dt.date | None = None
    entry_eligible_until: dt.date | None = None
    candidate_tags: list[str] = Field(default_factory=list)
    d1_confirmation: D1Confirmation | None = None
    d1_gap_retention: float | None = None
    d1_close_location: float | None = None
    d1_stability: float | None = None
    observed_dates: list[dt.date] = Field(default_factory=list)
    data_last_updated_at: str | None = None
    calendar_source: str | None = None
    universe_mode: str = "current_snapshot"
    survivorship_bias: bool = True
    security_master_pit: bool = False
    recommendation_kind: str = "rule_based_observation"
    risk_disclosure: str = "规则筛选结果仅供研究观察，不构成投资建议或收益承诺。"


class DetectionResult(BaseModel):
    triggered: bool
    signal: StrategySignal | None = None
    checks: list[RuleCheck] = Field(default_factory=list)
    exclusion_reasons: list[str] = Field(default_factory=list)


class Strategy(ABC):
    @abstractmethod
    def metadata(self) -> StrategyMetadata: ...

    @abstractmethod
    def config_snapshot(self) -> dict[str, Any]: ...

    @abstractmethod
    def config_schema(self) -> dict[str, Any]: ...

    @abstractmethod
    def config_hash(self) -> str: ...

    @abstractmethod
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
    ) -> DetectionResult: ...

    @abstractmethod
    def advance(
        self,
        signal: StrategySignal,
        raw_bars: pd.DataFrame,
        calendar: CalendarService,
        as_of: dt.date,
    ) -> StrategySignal: ...

    def explain(self, signal: StrategySignal) -> dict[str, Any]:
        return {
            "reasons": signal.reasons,
            "rule_checks": [check.model_dump(mode="json") for check in signal.rule_checks],
            "score_components": signal.score_components,
            "risk_flags": signal.risk_flags,
        }
