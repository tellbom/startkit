from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_strategy_api.core.errors import InvalidDateError
from stock_strategy_api.market_data.calendar import CalendarService
from tests.conftest import build_calendar_frame


def test_calendar_nth_day_crosses_weekend(tmp_path):
    service = CalendarService(tmp_path)
    service.save_fixture(build_calendar_frame(dt.date(2026, 6, 26), periods=10))
    friday = dt.date(2026, 6, 26)
    assert service.nth_trading_day_after(friday, 0) == friday
    assert service.nth_trading_day_after(friday, 1) == dt.date(2026, 6, 29)
    assert service.nth_trading_day_after(friday, 3) == dt.date(2026, 7, 1)


def test_calendar_respects_explicit_long_holiday(tmp_path):
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-09-30", "2026-10-12").date,
            "is_trading": [True, *([False] * 11), True],
            "source": "fixture",
        }
    )
    service = CalendarService(tmp_path)
    service.save_fixture(frame)
    assert service.next_trading_day(dt.date(2026, 9, 30)) == dt.date(2026, 10, 12)
    with pytest.raises(InvalidDateError):
        service.nth_trading_day_after(dt.date(2026, 10, 12), 1)
