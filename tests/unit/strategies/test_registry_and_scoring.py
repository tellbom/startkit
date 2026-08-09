from __future__ import annotations

import pandas as pd
import pytest

from stock_strategy_api.core.errors import UnknownStrategyError
from stock_strategy_api.strategies.registry import StrategyRegistry
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy
from stock_strategy_api.strategies.strong_gap_up_v1.scoring import build_rule_score, classify_phase


def test_registry_rejects_duplicate_and_unknown():
    registry = StrategyRegistry()
    registry.register(StrongGapUpStrategy())
    with pytest.raises(ValueError):
        registry.register(StrongGapUpStrategy())
    with pytest.raises(UnknownStrategyError):
        registry.get("missing")


def test_phase_classifies_first_and_second_unfilled_gap(d0):
    config = StrongGapUpStrategy().config
    dates = list(pd.bdate_range(end=d0, periods=5).date)
    no_prior_gap = pd.DataFrame({"date": dates, "low": [10, 10, 10, 10, 11], "high": [10.1] * 5})
    one_prior_gap = pd.DataFrame(
        {
            "date": dates,
            "low": [10.0, 10.3, 10.3, 10.3, 11.0],
            "high": [10.1, 10.4, 10.4, 10.4, 11.1],
        }
    )
    assert classify_phase(no_prior_gap, d0, config) == "persistent_candidate"
    assert classify_phase(one_prior_gap, d0, config) == "accelerating_candidate"


def test_scoring_clips_extreme_values_deterministically():
    config = StrongGapUpStrategy().config
    first = build_rule_score(
        gap_pct=999,
        volume_ratio=float("inf"),
        close_location=1,
        rise_return=999,
        platform_amplitude=0,
        config=config,
    )
    second = build_rule_score(
        gap_pct=999,
        volume_ratio=float("inf"),
        close_location=1,
        rise_return=999,
        platform_amplitude=0,
        config=config,
    )
    assert first == second
    assert first[0] == 100.0
