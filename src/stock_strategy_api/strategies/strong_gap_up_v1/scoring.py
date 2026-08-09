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


def build_rule_score(
    *,
    gap_pct: float,
    volume_ratio: float,
    close_location: float,
    rise_return: float,
    platform_amplitude: float,
    config: StrongGapConfig,
) -> tuple[float, dict[str, float]]:
    components = {
        "gap": _clip(gap_pct / config.score_gap_cap),
        "volume": _clip(volume_ratio / config.score_volume_cap),
        "close_location": _clip(close_location),
        "rise": _clip(rise_return / config.score_rise_cap),
        "platform_tightness": 1.0 - _clip(platform_amplitude / config.maximum_platform_amplitude),
    }
    weighted = (
        components["gap"] * 0.30
        + components["volume"] * 0.25
        + components["close_location"] * 0.20
        + components["rise"] * 0.15
        + components["platform_tightness"] * 0.10
    )
    return round(weighted * 100, 2), {key: round(value, 6) for key, value in components.items()}


def _prepare(frame: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"]).dt.date
    return result[result["date"] <= as_of].sort_values("date").drop_duplicates("date", keep="last")


def _clip(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
