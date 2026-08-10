from __future__ import annotations

import pandas as pd

from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.strategies.base import D1Confirmation, GapPhase
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy
from stock_strategy_api.strategies.strong_gap_up_v1.config import StrongGapConfig
from tests.conftest import build_calendar_frame


def _signal(strategy, qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    result = strategy.detect(
        raw, qfq, d0, eligible, universe_mode="point_in_time", survivorship_bias=False, calendar_source="fixture"
    )
    assert result.signal
    return result.signal, raw


def _calendar(tmp_path, d0):
    calendar = CalendarService(tmp_path)
    calendar.save_fixture(build_calendar_frame(d0, periods=12))
    return calendar


def _d1_bar(day, *, low, high, close, volume=100):
    return pd.DataFrame([{"date": day, "open": low + 0.02, "high": high, "low": low, "close": close, "volume": volume}])


def test_d1_fully_unfilled_grants_d2_entry(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    future = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.3, close=signal.gap_top + 0.2)

    result = strategy.advance(signal, pd.concat([raw, future]), calendar, d1)

    assert result.state == "entry_eligible"
    assert result.d1_confirmation == D1Confirmation.FULLY_UNFILLED
    assert result.confirmation_date == d1
    assert result.earliest_entry_date == d2
    assert result.entry_eligible_until == d2
    assert result.d1_score is not None
    assert result.rule_score != result.d0_score


def test_equal_floor_invalidates_and_is_terminal(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    future = _d1_bar(d1, low=signal.gap_floor, high=signal.gap_top + 0.2, close=signal.gap_top)
    result = strategy.advance(signal, pd.concat([raw, future]), calendar, d1)
    assert result.state == "invalidated"
    assert result.d1_confirmation == D1Confirmation.FULLY_FILLED
    assert strategy.advance(result, pd.DataFrame(), calendar, calendar.next_trading_day(d1)).state == "invalidated"


def test_missing_d1_bar_is_indeterminate(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    result = strategy.advance(signal, raw, calendar, calendar.next_trading_day(d0))
    assert result.state == "indeterminate"
    assert result.d1_confirmation == D1Confirmation.INDETERMINATE


def test_partial_fill_reclaimed_grants_entry_with_risk(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    middle = (signal.gap_floor + signal.gap_top) / 2
    future = _d1_bar(d1, low=middle, high=signal.gap_top + 0.2, close=signal.gap_top)

    result = strategy.advance(signal, pd.concat([raw, future]), calendar, d1)

    assert result.state == "entry_eligible"
    assert result.d1_confirmation == D1Confirmation.PARTIAL_RECLAIMED
    assert "partial_fill_reclaimed" in result.risk_flags
    assert 0 < result.remaining_gap_pct < 1


def test_partial_fill_not_reclaimed_is_weak_d1(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    middle = (signal.gap_floor + signal.gap_top) / 2
    future = _d1_bar(d1, low=middle, high=signal.gap_top + 0.1, close=signal.gap_top - 0.01)

    result = strategy.advance(signal, pd.concat([raw, future]), calendar, d1)

    assert result.state == "weak_d1"
    assert result.d1_confirmation == D1Confirmation.PARTIAL_WEAK
    assert result.earliest_entry_date is None


def test_equal_gap_top_is_fully_unfilled(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    future = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.1, close=signal.gap_top + 0.05)
    result = strategy.advance(signal, pd.concat([raw, future]), calendar, d1)
    assert result.d1_confirmation == D1Confirmation.FULLY_UNFILLED


def test_suspended_d1_is_indeterminate(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    future = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top, close=signal.gap_top, volume=0)
    result = strategy.advance(signal, pd.concat([raw, future]), calendar, d1)
    assert result.state == "indeterminate"
    assert "suspended_or_invalid_d1_bar" in result.risk_flags


def test_d2_acceptance_grants_independent_d3_continuation_entry(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    d1_frame = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.2, close=signal.gap_top + 0.1)
    eligible_signal = strategy.advance(signal, pd.concat([raw, d1_frame]), calendar, d1)
    d2_close = signal.close * 1.05
    d2_frame = _d1_bar(d2, low=signal.gap_top, high=d2_close + 0.1, close=d2_close)

    continuation = strategy.advance(eligible_signal, pd.concat([raw, d1_frame, d2_frame]), calendar, d2)

    assert continuation.state == "continuation_entry"
    assert continuation.structure_validity is True
    assert continuation.entry_validity is True
    assert continuation.entry_kind == "continuation_d3"
    assert continuation.normal_entry_window_closed_date == d2
    assert continuation.continuation_watch_date == d2
    assert continuation.continuation_entry_date == calendar.next_trading_day(d2)
    assert "d3_continuation_entry_granted" in continuation.reasons


def test_exact_ten_percent_d2_expansion_rejects_entry_but_keeps_structure(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    d1_frame = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.2, close=signal.gap_top + 0.1)
    eligible_signal = strategy.advance(signal, pd.concat([raw, d1_frame]), calendar, d1)
    d2_close = signal.close * 1.10
    d2_frame = _d1_bar(d2, low=signal.gap_top, high=d2_close + 0.1, close=d2_close)

    expired = strategy.advance(eligible_signal, pd.concat([raw, d1_frame, d2_frame]), calendar, d2)

    assert expired.state == "expired"
    assert expired.structure_validity is True
    assert expired.entry_validity is False
    assert expired.entry_invalid_reason == "overextended"
    assert expired.d2_expansion_from_d0_close == 0.10
    assert expired.invalidated_date is None


def test_expansion_just_below_ten_percent_remains_eligible(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    d1_frame = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.2, close=signal.gap_top + 0.1)
    eligible_signal = strategy.advance(signal, pd.concat([raw, d1_frame]), calendar, d1)
    d2_close = signal.close * 1.099999
    d2_frame = _d1_bar(d2, low=signal.gap_top, high=d2_close + 0.1, close=d2_close)

    continuation = strategy.advance(eligible_signal, pd.concat([raw, d1_frame, d2_frame]), calendar, d2)

    assert continuation.state == "continuation_entry"
    assert continuation.d2_expansion_from_d0_close == 0.099999


def test_weak_d2_close_rejects_continuation_without_invalidating_structure(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    d1_frame = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.2, close=signal.gap_top + 0.1)
    eligible_signal = strategy.advance(signal, pd.concat([raw, d1_frame]), calendar, d1)
    d2_frame = _d1_bar(d2, low=signal.gap_top, high=signal.gap_top + 1.0, close=signal.gap_top + 0.1)

    expired = strategy.advance(eligible_signal, pd.concat([raw, d1_frame, d2_frame]), calendar, d2)

    assert expired.state == "expired"
    assert expired.structure_validity is True
    assert expired.entry_validity is False
    assert expired.entry_invalid_reason == "weak_d2_close"


def test_exhaustion_phase_rejects_continuation_without_invalidating_structure(
    tmp_path, qualifying_frames, d0, eligible
):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    signal.phase = GapPhase.EXHAUSTION
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    d1_frame = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.2, close=signal.gap_top + 0.1)
    eligible_signal = strategy.advance(signal, pd.concat([raw, d1_frame]), calendar, d1)
    d2_close = signal.close * 1.05
    d2_frame = _d1_bar(d2, low=signal.gap_top, high=d2_close + 0.1, close=d2_close)

    expired = strategy.advance(eligible_signal, pd.concat([raw, d1_frame, d2_frame]), calendar, d2)

    assert expired.state == "expired"
    assert expired.structure_validity is True
    assert expired.entry_invalid_reason == "exhaustion"


def test_continuation_entry_expires_after_d3_without_destroying_structure(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    d3 = calendar.next_trading_day(d2)
    d1_frame = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.2, close=signal.gap_top + 0.1)
    eligible_signal = strategy.advance(signal, pd.concat([raw, d1_frame]), calendar, d1)
    d2_close = signal.close * 1.05
    d2_frame = _d1_bar(d2, low=signal.gap_top, high=d2_close + 0.1, close=d2_close)
    continuation = strategy.advance(eligible_signal, pd.concat([raw, d1_frame, d2_frame]), calendar, d2)
    d3_frame = _d1_bar(d3, low=signal.gap_top, high=d2_close + 0.2, close=d2_close + 0.1)

    expired = strategy.advance(continuation, pd.concat([raw, d1_frame, d2_frame, d3_frame]), calendar, d3)

    assert expired.state == "expired"
    assert expired.structure_validity is True
    assert expired.entry_validity is False
    assert expired.entry_invalid_reason == "continuation_entry_window_closed"


def test_full_fill_on_entry_day_invalidates(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    d1_frame = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.2, close=signal.gap_top + 0.1)
    eligible_signal = strategy.advance(signal, pd.concat([raw, d1_frame]), calendar, d1)
    d2_frame = _d1_bar(d2, low=signal.gap_floor, high=signal.gap_top, close=signal.gap_floor + 0.01)

    invalidated = strategy.advance(eligible_signal, pd.concat([raw, d1_frame, d2_frame]), calendar, d2)

    assert invalidated.state == "invalidated"
    assert invalidated.invalidated_date == d2
    assert invalidated.structure_validity is False
    assert invalidated.entry_validity is False
    assert invalidated.entry_invalid_reason == "gap_fully_filled"


def test_explicit_d3_variant_stays_eligible_only_when_d2_is_untradable(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy(StrongGapConfig(max_entry_wait_days=2))
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = _calendar(tmp_path, d0)
    d1 = calendar.next_trading_day(d0)
    d2 = calendar.next_trading_day(d1)
    d1_frame = _d1_bar(d1, low=signal.gap_top, high=signal.gap_top + 0.2, close=signal.gap_top + 0.1)
    eligible_signal = strategy.advance(signal, pd.concat([raw, d1_frame]), calendar, d1)
    previous_close = float(d1_frame.iloc[-1]["close"])
    d2_frame = pd.DataFrame(
        [
            {
                "date": d2,
                "open": previous_close + 1,
                "high": previous_close + 1,
                "low": previous_close + 1,
                "close": previous_close + 1,
                "volume": 100,
            }
        ]
    )

    delayed = strategy.advance(eligible_signal, pd.concat([raw, d1_frame, d2_frame]), calendar, d2)

    assert delayed.state == "entry_eligible"
    assert delayed.entry_eligible_until == calendar.next_trading_day(d2)
    assert "entry_day_one_price_up" in delayed.risk_flags
