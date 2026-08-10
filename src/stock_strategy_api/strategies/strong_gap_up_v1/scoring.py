from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_strategy_api.strategies.base import GapPhase
from stock_strategy_api.strategies.strong_gap_up_v1.config import StrongGapConfig


def classify_phase(raw_history: pd.DataFrame, as_of: dt.date, config: StrongGapConfig) -> GapPhase:
    history = _prepare(raw_history, as_of)
    if len(history) < 3:
        return GapPhase.PERSISTENT
    prior = history.iloc[:-1].tail(config.gap_history_days + 1).reset_index(drop=True)
    unfilled = 0
    for index in range(1, len(prior)):
        floor = float(prior.iloc[index - 1]["high"])
        ceiling = float(prior.iloc[index]["low"])
        if floor <= 0 or ceiling / floor - 1 < config.minimum_gap_pct:
            continue
        subsequent = prior.iloc[index + 1 :]
        if subsequent.empty or float(subsequent["low"].min()) > floor:
            unfilled += 1
    current_gap_number = unfilled + 1
    if current_gap_number >= config.exhaustion_gap_count:
        return GapPhase.EXHAUSTION
    if current_gap_number == 2:
        return GapPhase.ACCELERATING
    return GapPhase.PERSISTENT


def build_d0_score(
    *,
    gap_pct: float,
    volume_ratio: float,
    close_location: float,
    rise_return: float,
    platform_amplitude: float,
    platform_drift: float,
    breakout_pct: float,
    phase: GapPhase,
    config: StrongGapConfig,
) -> tuple[float, dict[str, float]]:
    components = {
        "gap": _clip(gap_pct / config.score_gap_cap),
        "volume": _clip(volume_ratio / config.score_volume_cap),
        "close_location": _clip(close_location),
        "breakout": _clip(breakout_pct / config.score_gap_cap),
        "rise": _clip(rise_return / config.score_rise_cap),
        "platform_quality": 0.5 * (1.0 - _clip(platform_amplitude / config.maximum_platform_amplitude))
        + 0.5 * (1.0 - _clip(platform_drift / config.maximum_platform_drift)),
        "phase_quality": {
            GapPhase.PERSISTENT: 1.0,
            GapPhase.ACCELERATING: 0.7,
            GapPhase.EXHAUSTION: 0.2,
        }[phase],
    }
    weighted = (
        components["gap"] * 0.20
        + components["volume"] * 0.20
        + components["close_location"] * 0.20
        + components["breakout"] * 0.15
        + components["rise"] * 0.10
        + components["platform_quality"] * 0.10
        + components["phase_quality"] * 0.05
    )
    return round(weighted * 100, 2), {key: round(value, 6) for key, value in components.items()}


def build_d1_score(
    *,
    gap_retention: float,
    close_location: float,
    stability: float,
    reclaimed: bool,
) -> tuple[float, dict[str, float]]:
    components = {
        "gap_retention": _clip(gap_retention),
        "reclaim": 1.0 if reclaimed else 0.0,
        "close_location": _clip(close_location),
        "stability": _clip(stability),
        "tradability": 1.0,
    }
    weighted = (
        components["gap_retention"] * 0.40
        + components["reclaim"] * 0.25
        + components["close_location"] * 0.20
        + components["stability"] * 0.10
        + components["tradability"] * 0.05
    )
    return round(weighted * 100, 2), {key: round(value, 6) for key, value in components.items()}


def combine_scores(
    d0_score: float,
    d1_score: float,
    d0_components: dict[str, float],
    d1_components: dict[str, float],
    config: StrongGapConfig,
) -> tuple[float, dict[str, float]]:
    d1_weight = 1.0 - config.d0_score_weight
    score = d0_score * config.d0_score_weight + d1_score * d1_weight
    components = {
        **{f"d0_{key}": value for key, value in d0_components.items()},
        **{f"d1_{key}": value for key, value in d1_components.items()},
        "d0_weight": config.d0_score_weight,
        "d1_weight": d1_weight,
    }
    return round(score, 2), components


def build_rule_score(
    *,
    gap_pct: float,
    volume_ratio: float,
    close_location: float,
    rise_return: float,
    platform_amplitude: float,
    config: StrongGapConfig,
) -> tuple[float, dict[str, float]]:
    """Compatibility wrapper for callers that only have the original D0 factors."""
    return build_d0_score(
        gap_pct=gap_pct,
        volume_ratio=volume_ratio,
        close_location=close_location,
        rise_return=rise_return,
        platform_amplitude=platform_amplitude,
        platform_drift=0.0,
        breakout_pct=config.score_gap_cap,
        phase=GapPhase.PERSISTENT,
        config=config,
    )


def _prepare(frame: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"]).dt.date
    return result[result["date"] <= as_of].sort_values("date").drop_duplicates("date", keep="last")


def _clip(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
