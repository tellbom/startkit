from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.strategies.base import SignalState, StrategySignal

_TERMINAL = {SignalState.INVALIDATED, SignalState.INDETERMINATE, SignalState.EXPIRED}


def advance_signal(
    signal: StrategySignal,
    raw_bars: pd.DataFrame,
    calendar: CalendarService,
    as_of: dt.date,
    max_entry_wait_days: int = 3,
) -> StrategySignal:
    if signal.state in _TERMINAL:
        return signal.model_copy(deep=True)
    if signal.state == SignalState.CONFIRMED:
        assert signal.confirmation_date is not None
        last_entry_day = calendar.nth_trading_day_after(signal.confirmation_date, max_entry_wait_days)
        if as_of > last_entry_day:
            result = signal.model_copy(deep=True)
            result.state = SignalState.EXPIRED
            result.reasons = _append_unique(result.reasons, "entry_window_expired")
            return result
        return signal.model_copy(deep=True)
    bars = raw_bars.copy()
    if not bars.empty:
        bars["date"] = pd.to_datetime(bars["date"], errors="coerce").dt.date
        bars = bars.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    result = signal.model_copy(deep=True)
    observed = set(result.observed_dates)
    for day_number in range(1, 4):
        day = calendar.nth_trading_day_after(signal.signal_date, day_number)
        if day > as_of or day in observed:
            continue
        if day not in bars.index:
            result.state = SignalState.INDETERMINATE
            result.risk_flags = _append_unique(result.risk_flags, "missing_confirmation_bar")
            return result
        row = bars.loc[day]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        if any(pd.isna(row.get(column)) for column in ("low", "volume")) or float(row["volume"]) <= 0:
            result.state = SignalState.INDETERMINATE
            result.risk_flags = _append_unique(result.risk_flags, "suspended_or_invalid_confirmation_bar")
            return result
        low = float(row["low"])
        result.observed_dates.append(day)
        observed.add(day)
        if low <= signal.gap_floor:
            result.state = SignalState.INVALIDATED
            result.invalidated_date = day
            result.remaining_gap_pct = 0.0
            result.reasons = _append_unique(result.reasons, "gap_fully_filled")
            return result
        remaining = (low - signal.gap_floor) / (signal.gap_ceiling - signal.gap_floor)
        result.remaining_gap_pct = round(max(0.0, min(remaining, 1.0)), 6)
        if low < signal.gap_ceiling:
            result.state = SignalState.PARTIALLY_FILLED
            result.risk_flags = _append_unique(result.risk_flags, "partial_fill")
        elif day_number == 1:
            result.state = SignalState.WATCHING_D1
        elif day_number == 2:
            result.state = SignalState.WATCHING_D2
        if day_number == 3:
            result.state = SignalState.CONFIRMED
            result.confirmation_date = day
            result.earliest_entry_date = calendar.next_trading_day(day)
            result.reasons = [reason for reason in result.reasons if reason != "three_day_confirmation_pending"]
            result.reasons = _append_unique(result.reasons, "three_trading_days_unfilled")
    return result


def _append_unique(values: list[str], value: str) -> list[str]:
    return values if value in values else [*values, value]
