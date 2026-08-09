from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field


class StrongGapConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_listing_days: int = Field(default=60, ge=1)
    required_prior_days: int = Field(default=40, ge=31)
    rise_window_days: int = Field(default=20, ge=2)
    platform_window_days: int = Field(default=10, ge=2)
    minimum_rise_return: float = Field(default=0.10, ge=0)
    maximum_platform_amplitude: float = Field(default=0.12, gt=0)
    maximum_platform_drift: float = Field(default=0.08, ge=0)
    minimum_gap_pct: float = Field(default=0.01, gt=0)
    volume_lookback_days: int = Field(default=20, ge=2)
    minimum_volume_ratio: float = Field(default=2.0, gt=0)
    minimum_close_location: float = Field(default=0.60, ge=0, le=1)
    confirmation_days: int = Field(default=3, ge=3, le=3)
    gap_history_days: int = Field(default=20, ge=3)
    exhaustion_gap_count: int = Field(default=3, ge=2)
    max_entry_wait_days: int = Field(default=3, ge=1)
    backtest_horizons: tuple[int, ...] = (1, 3, 5, 10)
    score_gap_cap: float = Field(default=0.05, gt=0)
    score_volume_cap: float = Field(default=4.0, gt=0)
    score_rise_cap: float = Field(default=0.30, gt=0)

    def digest(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]
