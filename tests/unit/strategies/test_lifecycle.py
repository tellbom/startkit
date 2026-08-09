from __future__ import annotations

import pandas as pd
import pytest

from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy
from tests.conftest import build_calendar_frame


def _signal(strategy, qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    result = strategy.detect(
        raw, qfq, d0, eligible, universe_mode="point_in_time", survivorship_bias=False, calendar_source="fixture"
    )
    assert result.signal
    return result.signal, raw


def test_three_complete_days_confirm_and_set_d4(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = CalendarService(tmp_path)
    calendar.save_fixture(build_calendar_frame(d0, periods=10))
    future = pd.DataFrame(
        [{"date": calendar.nth_trading_day_after(d0, i), "low": 10.75 + i * 0.01, "volume": 100} for i in (1, 2, 3)]
    )
    result = strategy.advance(signal, pd.concat([raw, future]), calendar, calendar.nth_trading_day_after(d0, 3))
    assert result.state == "confirmed"
    assert result.confirmation_date == calendar.nth_trading_day_after(d0, 3)
    assert result.earliest_entry_date == calendar.nth_trading_day_after(d0, 4)


def test_equal_floor_invalidates_and_is_terminal(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = CalendarService(tmp_path)
    calendar.save_fixture(build_calendar_frame(d0, periods=10))
    d1 = calendar.next_trading_day(d0)
    future = pd.DataFrame([{"date": d1, "low": signal.gap_floor, "volume": 100}])
    result = strategy.advance(signal, pd.concat([raw, future]), calendar, d1)
    assert result.state == "invalidated"
    assert (
        strategy.advance(result, pd.DataFrame(), calendar, calendar.nth_trading_day_after(d0, 3)).state == "invalidated"
    )


def test_missing_confirmation_bar_is_indeterminate(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = CalendarService(tmp_path)
    calendar.save_fixture(build_calendar_frame(d0, periods=10))
    result = strategy.advance(signal, raw, calendar, calendar.next_trading_day(d0))
    assert result.state == "indeterminate"


def test_partial_fill_stays_observable_and_can_confirm(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = CalendarService(tmp_path)
    calendar.save_fixture(build_calendar_frame(d0, periods=10))
    middle = (signal.gap_floor + signal.gap_ceiling) / 2
    future = pd.DataFrame(
        [
            {"date": calendar.nth_trading_day_after(d0, 1), "low": middle, "volume": 100},
            {"date": calendar.nth_trading_day_after(d0, 2), "low": signal.gap_ceiling + 0.1, "volume": 100},
            {"date": calendar.nth_trading_day_after(d0, 3), "low": signal.gap_ceiling + 0.1, "volume": 100},
        ]
    )
    d1_result = strategy.advance(signal, pd.concat([raw, future]), calendar, calendar.nth_trading_day_after(d0, 1))
    assert d1_result.state == "partially_filled"
    assert 0 < d1_result.remaining_gap_pct < 1
    final = strategy.advance(d1_result, pd.concat([raw, future]), calendar, calendar.nth_trading_day_after(d0, 3))
    assert final.state == "confirmed"
    assert "partial_fill" in final.risk_flags


@pytest.mark.parametrize("fill_day", [1, 2, 3])
def test_full_fill_on_each_confirmation_day_invalidates(tmp_path, qualifying_frames, d0, eligible, fill_day):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = CalendarService(tmp_path)
    calendar.save_fixture(build_calendar_frame(d0, periods=10))
    future = pd.DataFrame(
        [
            {
                "date": calendar.nth_trading_day_after(d0, index),
                "low": signal.gap_floor if index == fill_day else signal.gap_ceiling + 0.1,
                "volume": 100,
            }
            for index in (1, 2, 3)
        ]
    )
    result = strategy.advance(
        signal,
        pd.concat([raw, future]),
        calendar,
        calendar.nth_trading_day_after(d0, 3),
    )
    assert result.state == "invalidated"
    assert result.invalidated_date == calendar.nth_trading_day_after(d0, fill_day)


def test_suspended_day_is_indeterminate(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = CalendarService(tmp_path)
    calendar.save_fixture(build_calendar_frame(d0, periods=10))
    d1 = calendar.next_trading_day(d0)
    future = pd.DataFrame([{"date": d1, "low": signal.gap_ceiling, "volume": 0}])
    result = strategy.advance(signal, pd.concat([raw, future]), calendar, d1)
    assert result.state == "indeterminate"
    assert "suspended_or_invalid_confirmation_bar" in result.risk_flags


def test_confirmed_signal_expires_after_entry_window(tmp_path, qualifying_frames, d0, eligible):
    strategy = StrongGapUpStrategy()
    signal, raw = _signal(strategy, qualifying_frames, d0, eligible)
    calendar = CalendarService(tmp_path)
    calendar.save_fixture(build_calendar_frame(d0, periods=12))
    future = pd.DataFrame(
        [{"date": calendar.nth_trading_day_after(d0, index), "low": 10.8, "volume": 100} for index in (1, 2, 3)]
    )
    confirmed = strategy.advance(signal, pd.concat([raw, future]), calendar, calendar.nth_trading_day_after(d0, 3))
    last_entry = calendar.nth_trading_day_after(confirmed.confirmation_date, 3)
    assert strategy.advance(confirmed, raw, calendar, last_entry).state == "confirmed"
    expired = strategy.advance(confirmed, raw, calendar, calendar.next_trading_day(last_entry))
    assert expired.state == "expired"
