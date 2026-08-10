from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrongGapConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_listing_days: int = Field(default=60, ge=1)
    required_prior_days: int = Field(default=40, ge=31)
    rise_window_days: int = Field(default=20, ge=2)
    platform_window_days: int = Field(default=10, ge=2)
    minimum_rise_return: float = Field(default=0.10, ge=0)
    maximum_platform_amplitude: float = Field(default=0.12, gt=0)
    maximum_platform_drift: float = Field(default=0.08, gt=0)
    minimum_gap_pct: float = Field(default=0.005, gt=0)
    volume_lookback_days: int = Field(default=20, ge=2)
    minimum_volume_ratio: float = Field(default=1.5, gt=0)
    minimum_close_location: float = Field(default=0.60, ge=0, le=1)
    strict_minimum_gap_pct: float = Field(default=0.01, gt=0)
    strict_minimum_volume_ratio: float = Field(default=2.0, gt=0)
    confirmation_days: int = Field(default=1, ge=1, le=1)
    gap_history_days: int = Field(default=20, ge=3)
    exhaustion_gap_count: int = Field(default=3, ge=2)
    max_entry_wait_days: int = Field(default=1, ge=1, le=2)
    continuation_max_expansion: float = Field(default=0.10, gt=0)
    continuation_minimum_close_location: float = Field(default=0.60, ge=0, le=1)
    continuation_entry_days: int = Field(default=1, ge=1, le=1)
    max_holding_days: int = Field(default=5, ge=4, le=5)
    backtest_horizons: tuple[int, ...] = (1, 2, 3, 4, 5)
    score_gap_cap: float = Field(default=0.05, gt=0)
    score_volume_cap: float = Field(default=4.0, gt=0)
    score_rise_cap: float = Field(default=0.30, gt=0)
    d0_score_weight: float = Field(default=0.60, gt=0, lt=1)

    @model_validator(mode="after")
    def validate_strategy_windows(self) -> StrongGapConfig:
        if self.strict_minimum_gap_pct < self.minimum_gap_pct:
            raise ValueError("strict gap threshold must not be below SHORT_GAP threshold")
        if self.strict_minimum_volume_ratio < self.minimum_volume_ratio:
            raise ValueError("strict volume threshold must not be below SHORT_GAP threshold")
        if tuple(sorted(set(self.backtest_horizons))) != self.backtest_horizons:
            raise ValueError("backtest horizons must be unique and increasing")
        if not self.backtest_horizons or self.backtest_horizons[0] < 1 or self.backtest_horizons[-1] > 5:
            raise ValueError("backtest horizons must stay within 1 to 5 trading days")
        if self.max_holding_days not in self.backtest_horizons:
            raise ValueError("maximum holding days must be included in backtest horizons")
        return self

    def digest(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]
