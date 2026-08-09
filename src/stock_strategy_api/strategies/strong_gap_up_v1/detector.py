from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from stock_strategy_api.market_data.security_master import EligibilityResult
from stock_strategy_api.strategies.base import DetectionResult, RuleCheck, SignalState, StrategySignal
from stock_strategy_api.strategies.strong_gap_up_v1.config import StrongGapConfig
from stock_strategy_api.strategies.strong_gap_up_v1.manifest import METADATA
from stock_strategy_api.strategies.strong_gap_up_v1.scoring import build_rule_score, classify_phase


def detect_signal(
    raw_history: pd.DataFrame,
    qfq_history: pd.DataFrame,
    as_of: dt.date,
    eligibility: EligibilityResult,
    config: StrongGapConfig,
    *,
    universe_mode: str,
    survivorship_bias: bool,
    calendar_source: str,
    expected_previous_trade_date: dt.date | None = None,
) -> DetectionResult:
    if not eligibility.eligible:
        return DetectionResult(triggered=False, exclusion_reasons=list(eligibility.reasons))
    raw = _prepare(raw_history, as_of)
    qfq = _prepare(qfq_history, as_of)
    if raw.empty or qfq.empty or raw.iloc[-1]["date"] != as_of or qfq.iloc[-1]["date"] != as_of:
        return DetectionResult(triggered=False, exclusion_reasons=["missing_as_of_bar"])
    raw_prior = raw.iloc[:-1]
    qfq_prior = qfq.iloc[:-1]
    if len(raw_prior) < config.required_prior_days or len(qfq_prior) < config.required_prior_days:
        return DetectionResult(triggered=False, exclusion_reasons=["insufficient_history"])

    qfq_by_date = qfq.set_index("date")
    if any(date not in qfq_by_date.index for date in raw["date"]):
        return DetectionResult(triggered=False, exclusion_reasons=["raw_qfq_date_mismatch"])

    d0 = raw.iloc[-1]
    previous = raw.iloc[-2]
    if expected_previous_trade_date is not None and previous["date"] != expected_previous_trade_date:
        return DetectionResult(triggered=False, exclusion_reasons=["missing_previous_trading_day_bar"])
    platform = raw_prior.tail(config.platform_window_days)
    qfq_platform = qfq_prior.tail(config.platform_window_days)
    rise_end_position = len(qfq_prior) - config.platform_window_days - 1
    rise_start_position = rise_end_position - config.rise_window_days + 1
    if rise_start_position < 0:
        return DetectionResult(triggered=False, exclusion_reasons=["insufficient_rise_window"])
    rise_start = float(qfq_prior.iloc[rise_start_position]["close"])
    rise_end = float(qfq_prior.iloc[rise_end_position]["close"])

    gap_floor = float(previous["high"])
    gap_ceiling = float(d0["low"])
    gap_pct = gap_ceiling / gap_floor - 1 if gap_floor > 0 else -1.0
    historical_volume = raw_prior.tail(config.volume_lookback_days)["volume"].astype(float)
    median_volume = float(historical_volume.median())
    volume_ratio = float(d0["volume"]) / median_volume if median_volume > 0 else 0.0
    high_low_range = float(d0["high"]) - float(d0["low"])
    one_price = high_low_range == 0
    close_location = 1.0 if one_price else (float(d0["close"]) - float(d0["low"])) / high_low_range
    rise_return = rise_end / rise_start - 1 if rise_start > 0 else -1.0
    platform_low = float(platform["low"].min())
    platform_amplitude = float(platform["high"].max()) / platform_low - 1 if platform_low > 0 else float("inf")
    platform_first = float(qfq_platform.iloc[0]["close"])
    platform_drift = (
        abs(float(qfq_platform.iloc[-1]["close"]) / platform_first - 1) if platform_first > 0 else float("inf")
    )
    platform_high = float(platform["high"].max())

    checks = [
        _check("gap_geometry", gap_ceiling > gap_floor, gap_ceiling, ">", gap_floor),
        _check("minimum_gap_pct", gap_pct >= config.minimum_gap_pct, gap_pct, ">=", config.minimum_gap_pct),
        _check(
            "minimum_volume_ratio",
            volume_ratio >= config.minimum_volume_ratio,
            volume_ratio,
            ">=",
            config.minimum_volume_ratio,
        ),
        _check(
            "bullish_candle",
            float(d0["close"]) > float(d0["open"]) or one_price,
            float(d0["close"]),
            "> or one_price",
            float(d0["open"]),
        ),
        _check(
            "minimum_close_location",
            close_location >= config.minimum_close_location,
            close_location,
            ">=",
            config.minimum_close_location,
        ),
        _check("platform_breakout", float(d0["close"]) > platform_high, float(d0["close"]), ">", platform_high),
        _check(
            "minimum_rise_return",
            rise_return >= config.minimum_rise_return,
            rise_return,
            ">=",
            config.minimum_rise_return,
        ),
        _check(
            "maximum_platform_amplitude",
            platform_amplitude <= config.maximum_platform_amplitude,
            platform_amplitude,
            "<=",
            config.maximum_platform_amplitude,
        ),
        _check(
            "maximum_platform_drift",
            platform_drift <= config.maximum_platform_drift,
            platform_drift,
            "<=",
            config.maximum_platform_drift,
        ),
    ]
    failed = [check.name for check in checks if not check.passed]
    if failed:
        return DetectionResult(triggered=False, checks=checks, exclusion_reasons=failed)

    phase = classify_phase(raw, as_of, config)
    score, components = build_rule_score(
        gap_pct=gap_pct,
        volume_ratio=volume_ratio,
        close_location=close_location,
        rise_return=rise_return,
        platform_amplitude=platform_amplitude,
        config=config,
    )
    risk_flags: list[str] = []
    if one_price:
        risk_flags.append("one_price_limit_risk")
    if calendar_source.startswith("exchange_calendars"):
        risk_flags.append("calendar_fallback_source")
    if phase.value == "accelerating_candidate":
        risk_flags.append("late_trend_risk")
    elif phase.value == "exhaustion_risk":
        risk_flags.extend(["late_trend_risk", "exhaustion_risk"])
    security = eligibility.security
    assert security is not None
    signal = StrategySignal(
        strategy_id=METADATA.strategy_id,
        strategy_version=METADATA.version,
        config_hash=config.digest(),
        symbol=security.symbol,
        stock_name=security.name,
        signal_date=as_of,
        state=SignalState.TRIGGERED,
        phase=phase,
        risk_flags=risk_flags,
        gap_floor=gap_floor,
        gap_ceiling=gap_ceiling,
        gap_pct=gap_pct,
        open=float(d0["open"]),
        high=float(d0["high"]),
        low=float(d0["low"]),
        close=float(d0["close"]),
        volume=float(d0["volume"]),
        amount=_optional_float(d0.get("amount")),
        volume_ratio=volume_ratio,
        close_location=close_location,
        rise_return=rise_return,
        platform_amplitude=platform_amplitude,
        platform_drift=platform_drift,
        rule_score=score,
        score_components=components,
        rule_checks=checks,
        reasons=["upward_gap", "volume_expansion", "prior_rise", "tight_platform", "three_day_confirmation_pending"],
        calendar_source=calendar_source,
        universe_mode=universe_mode,
        survivorship_bias=survivorship_bias,
        security_master_pit=eligibility.security_master_pit,
        risk_disclosure=METADATA.risk_disclosure,
    )
    return DetectionResult(triggered=True, signal=signal, checks=checks)


def _prepare(frame: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=sorted(required))
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date
    result = result[result["date"] <= as_of].sort_values("date").drop_duplicates("date", keep="last")
    return result.dropna(subset=list(required)).reset_index(drop=True)


def _check(name: str, passed: bool, actual: Any, operator: str, threshold: Any) -> RuleCheck:
    return RuleCheck(
        name=name, passed=bool(passed), actual=_finite(actual), operator=operator, threshold=_finite(threshold)
    )


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not pd.notna(value):
        return None
    if isinstance(value, float) and value in {float("inf"), float("-inf")}:
        return None
    return value


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None and pd.notna(value) else None
