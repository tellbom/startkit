from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.strategies.base import D1Confirmation, SignalState, StrategySignal
from stock_strategy_api.strategies.strong_gap_up_v1.config import StrongGapConfig
from stock_strategy_api.strategies.strong_gap_up_v1.scoring import build_d1_score, combine_scores

_TERMINAL = {SignalState.INVALIDATED, SignalState.INDETERMINATE, SignalState.EXPIRED, SignalState.WEAK_D1}


def advance_signal(
    signal: StrategySignal,
    raw_bars: pd.DataFrame,
    calendar: CalendarService,
    as_of: dt.date,
    config: StrongGapConfig,
) -> StrategySignal:
    if signal.state in _TERMINAL:
        return signal.model_copy(deep=True)
    bars = _prepare(raw_bars)
    if signal.state == SignalState.ENTRY_ELIGIBLE:
        return _advance_entry_window(signal, bars, calendar, as_of, config)
    if signal.state not in {SignalState.TRIGGERED, SignalState.WATCHING_D1, SignalState.PARTIALLY_FILLED}:
        return signal.model_copy(deep=True)

    d1 = calendar.next_trading_day(signal.signal_date)
    if d1 > as_of:
        return signal.model_copy(deep=True)
    result = signal.model_copy(deep=True)
    if d1 not in bars.index:
        result.state = SignalState.INDETERMINATE
        result.d1_confirmation = D1Confirmation.INDETERMINATE
        result.risk_flags = _append_unique(result.risk_flags, "missing_d1_bar")
        return result
    row = _row(bars, d1)
    if any(pd.isna(row.get(column)) for column in ("high", "low", "close", "volume")) or float(row["volume"]) <= 0:
        result.state = SignalState.INDETERMINATE
        result.d1_confirmation = D1Confirmation.INDETERMINATE
        result.risk_flags = _append_unique(result.risk_flags, "suspended_or_invalid_d1_bar")
        return result

    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    if high < low or close < low or close > high:
        result.state = SignalState.INDETERMINATE
        result.d1_confirmation = D1Confirmation.INDETERMINATE
        result.risk_flags = _append_unique(result.risk_flags, "invalid_d1_price_geometry")
        return result
    gap_top = float(signal.gap_top if signal.gap_top is not None else signal.gap_ceiling)
    gap_width = gap_top - signal.gap_floor
    result.observed_dates = _append_unique(result.observed_dates, d1)
    result.d1_gap_retention = round(_clip((low - signal.gap_floor) / gap_width if gap_width > 0 else 0.0), 6)
    result.remaining_gap_pct = result.d1_gap_retention
    intraday_range = high - low
    result.d1_close_location = round(1.0 if intraday_range == 0 else _clip((close - low) / intraday_range), 6)
    result.d1_stability = round(1.0 if close <= 0 else _clip(1.0 - intraday_range / close), 6)

    if low <= signal.gap_floor:
        result.state = SignalState.INVALIDATED
        result.d1_confirmation = D1Confirmation.FULLY_FILLED
        result.invalidated_date = d1
        result.remaining_gap_pct = 0.0
        result.reasons = _append_unique(result.reasons, "d1_gap_fully_filled")
        return result

    partial = low < gap_top
    reclaimed = close >= gap_top
    d1_score, d1_components = build_d1_score(
        gap_retention=result.d1_gap_retention,
        close_location=result.d1_close_location,
        stability=result.d1_stability,
        reclaimed=reclaimed,
    )
    result.d1_score = d1_score
    d0_score = float(signal.d0_score if signal.d0_score is not None else signal.rule_score)
    d0_components = {
        key.removeprefix("d0_"): value for key, value in signal.score_components.items() if key.startswith("d0_")
    }
    result.rule_score, result.score_components = combine_scores(
        d0_score,
        d1_score,
        d0_components,
        d1_components,
        config,
    )
    if partial and not reclaimed:
        result.state = SignalState.WEAK_D1
        result.d1_confirmation = D1Confirmation.PARTIAL_WEAK
        result.risk_flags = _append_unique(result.risk_flags, "partial_fill_not_reclaimed")
        result.reasons = _append_unique(result.reasons, "d1_weak_acceptance")
        return result

    result.state = SignalState.ENTRY_ELIGIBLE
    result.confirmation_date = d1
    result.earliest_entry_date = calendar.next_trading_day(d1)
    result.entry_eligible_until = calendar.nth_trading_day_after(d1, config.max_entry_wait_days)
    result.reasons = [reason for reason in result.reasons if reason != "d1_confirmation_pending"]
    if partial:
        result.d1_confirmation = D1Confirmation.PARTIAL_RECLAIMED
        result.risk_flags = _append_unique(result.risk_flags, "partial_fill_reclaimed")
        result.reasons = _append_unique(result.reasons, "d1_partial_fill_reclaimed")
    else:
        result.d1_confirmation = D1Confirmation.FULLY_UNFILLED
        result.reasons = _append_unique(result.reasons, "d1_gap_fully_held")
    return result


def _advance_entry_window(
    signal: StrategySignal,
    bars: pd.DataFrame,
    calendar: CalendarService,
    as_of: dt.date,
    config: StrongGapConfig,
) -> StrategySignal:
    result = signal.model_copy(deep=True)
    assert signal.confirmation_date is not None
    assert signal.earliest_entry_date is not None
    last_entry = signal.entry_eligible_until or calendar.nth_trading_day_after(
        signal.confirmation_date, config.max_entry_wait_days
    )
    for offset in range(1, config.max_entry_wait_days + 1):
        day = calendar.nth_trading_day_after(signal.confirmation_date, offset)
        if day > as_of or day in result.observed_dates:
            continue
        if day not in bars.index:
            result.risk_flags = _append_unique(result.risk_flags, "entry_day_bar_missing")
            continue
        row = _row(bars, day)
        if pd.isna(row.get("low")) or pd.isna(row.get("volume")) or float(row["volume"]) <= 0:
            result.risk_flags = _append_unique(result.risk_flags, "entry_day_untradable")
            continue
        if _is_one_price_up(bars, day, row):
            result.risk_flags = _append_unique(result.risk_flags, "entry_day_one_price_up")
            continue
        result.observed_dates = _append_unique(result.observed_dates, day)
        if float(row["low"]) <= signal.gap_floor:
            result.state = SignalState.INVALIDATED
            result.invalidated_date = day
            result.remaining_gap_pct = 0.0
            result.reasons = _append_unique(result.reasons, "post_confirmation_gap_fully_filled")
            return result
        result.state = SignalState.EXPIRED
        result.reasons = _append_unique(result.reasons, "first_tradable_entry_day_closed")
        return result
    if as_of >= last_entry:
        result.state = SignalState.EXPIRED
        result.reasons = _append_unique(result.reasons, "entry_window_closed")
    return result


def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    bars = frame.copy()
    if bars.empty:
        return bars
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce").dt.date
    return bars.sort_values("date").drop_duplicates("date", keep="last").set_index("date")


def _row(bars: pd.DataFrame, day: dt.date) -> pd.Series:
    row = bars.loc[day]
    return row.iloc[-1] if isinstance(row, pd.DataFrame) else row


def _is_one_price_up(bars: pd.DataFrame, day: dt.date, row: pd.Series) -> bool:
    if any(pd.isna(row.get(column)) for column in ("open", "high", "low")):
        return False
    if not (float(row["open"]) == float(row["high"]) == float(row["low"])):
        return False
    previous = bars.loc[bars.index < day]
    if previous.empty or pd.isna(previous.iloc[-1].get("close")):
        return False
    return float(row["open"]) > float(previous.iloc[-1]["close"])


def _append_unique(values: list, value):
    return values if value in values else [*values, value]


def _clip(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
