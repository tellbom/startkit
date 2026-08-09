from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_strategy_api.strategies.base import GapPhase
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy
from stock_strategy_api.strategies.strong_gap_up_v1.scoring import classify_phase


def _detect(strategy, raw, qfq, d0, eligible):
    return strategy.detect(
        raw,
        qfq,
        d0,
        eligible,
        universe_mode="point_in_time",
        survivorship_bias=False,
        calendar_source="fixture",
    )


def test_qualifying_signal_uses_raw_gap_and_qfq_trend(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    result = _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible)
    assert result.triggered
    assert result.signal is not None
    assert result.signal.gap_floor == raw.iloc[-2]["high"]
    assert result.signal.volume_ratio == 3.0
    assert result.signal.state == "triggered"


def test_equal_previous_high_is_not_a_gap(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    raw.loc[raw.index[-1], "low"] = raw.iloc[-2]["high"]
    result = _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible)
    assert not result.triggered
    assert "gap_geometry" in result.exclusion_reasons


def test_detector_ignores_future_rows(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    baseline = _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible)
    future = pd.DataFrame(
        [
            {
                "symbol": "600000",
                "date": d0 + dt.timedelta(days=1),
                "open": 100,
                "high": 200,
                "low": 1,
                "close": 2,
                "volume": 999999,
            }
        ]
    )
    with_future = _detect(StrongGapUpStrategy(), pd.concat([raw, future]), pd.concat([qfq, future]), d0, eligible)
    assert with_future.model_dump() == baseline.model_dump()


def test_d0_volume_is_not_in_baseline(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    raw.loc[raw.index[-1], "volume"] = 200
    result = _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible)
    assert result.triggered
    assert result.signal.volume_ratio == 2.0


def test_gap_percentage_boundary(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    floor = float(raw.iloc[-2]["high"])
    raw.loc[raw.index[-1], "low"] = floor * 1.0099
    assert not _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible).triggered
    raw.loc[raw.index[-1], "low"] = floor * 1.01
    result = _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible)
    assert result.triggered


def test_third_unfilled_gap_is_exhaustion(d0):
    dates = list(pd.bdate_range(end=d0, periods=10).date)
    lows = [9.9, 10.0, 10.3, 10.25, 10.5, 10.9, 10.85, 11.0, 11.1, 11.4]
    highs = [10.0, 10.1, 10.5, 10.45, 10.7, 11.1, 11.05, 11.2, 11.3, 11.6]
    frame = pd.DataFrame({"date": dates, "low": lows, "high": highs})
    assert classify_phase(frame, d0, StrongGapUpStrategy().config) == GapPhase.EXHAUSTION


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("rise", "minimum_rise_return"),
        ("platform", "maximum_platform_amplitude"),
        ("platform_drift", "maximum_platform_drift"),
        ("bearish", "bullish_candle"),
        ("close_location", "minimum_close_location"),
    ],
)
def test_each_major_hard_filter_has_a_stable_reason(qualifying_frames, d0, eligible, mutation, reason):
    raw, qfq = qualifying_frames
    if mutation == "rise":
        qfq.loc[qfq.index[:30], "close"] = 10.0
    elif mutation == "platform":
        raw.loc[raw.index[-5], ["high", "low"]] = [12.0, 9.0]
    elif mutation == "platform_drift":
        qfq.loc[qfq.index[-11], "close"] = 9.0
    elif mutation == "bearish":
        raw.loc[raw.index[-1], "open"] = 11.55
    else:
        raw.loc[raw.index[-1], "close"] = 10.90
    result = _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible)
    assert not result.triggered
    assert reason in result.exclusion_reasons


def test_adjusted_prices_never_define_gap_geometry(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    qfq.loc[qfq.index[-1], "low"] = 1.0
    result = _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible)
    assert result.triggered
    assert result.signal is not None
    assert result.signal.gap_ceiling == raw.iloc[-1]["low"]


def test_missing_history_has_stable_reason(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    result = _detect(StrongGapUpStrategy(), raw.tail(20), qfq.tail(20), d0, eligible)
    assert result.exclusion_reasons == ["insufficient_history"]


def test_one_price_d0_is_flagged_for_execution_risk(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    raw.loc[raw.index[-1], ["open", "high", "low", "close"]] = 11.0
    result = _detect(StrongGapUpStrategy(), raw, qfq, d0, eligible)
    assert result.triggered
    assert result.signal is not None
    assert result.signal.close_location == 1.0
    assert "one_price_limit_risk" in result.signal.risk_flags


def test_missing_calendar_defined_previous_bar_fails(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    result = StrongGapUpStrategy().detect(
        raw,
        qfq,
        d0,
        eligible,
        universe_mode="point_in_time",
        survivorship_bias=False,
        calendar_source="fixture",
        expected_previous_trade_date=d0 - dt.timedelta(days=2),
    )
    assert result.exclusion_reasons == ["missing_previous_trading_day_bar"]
