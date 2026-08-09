from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_strategy_api.market_data.security_master import EligibilityResult, SecuritySnapshot


@pytest.fixture
def d0() -> dt.date:
    return dt.date(2026, 6, 30)


@pytest.fixture
def qualifying_frames(d0: dt.date) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = list(pd.bdate_range(end=d0, periods=41).date)
    rows = []
    for index, date in enumerate(dates[:-1]):
        if index < 30:
            close = 8.0 + index * 0.09
        else:
            close = 10.55 + ((index % 3) - 1) * 0.02
        rows.append(
            {
                "symbol": "600000",
                "date": date,
                "open": close - 0.02,
                "high": close + 0.05,
                "low": close - 0.05,
                "close": close,
                "volume": 100.0,
                "amount": 1000.0,
            }
        )
    rows.append(
        {
            "symbol": "600000",
            "date": d0,
            "open": 10.82,
            "high": 11.60,
            "low": 10.80,
            "close": 11.50,
            "volume": 300.0,
            "amount": 4000.0,
        }
    )
    raw = pd.DataFrame(rows)
    qfq = raw.copy()
    for column in ("open", "high", "low", "close"):
        qfq[column] *= 1.1
    return raw, qfq


@pytest.fixture
def eligible(d0: dt.date) -> EligibilityResult:
    security = SecuritySnapshot(
        symbol="600000",
        name="浦发银行",
        exchange="sh",
        listing_date=dt.date(1999, 11, 10),
        status="active",
        effective_date=d0,
        source="fixture",
    )
    return EligibilityResult(True, (), security, True)


def build_calendar_frame(start: dt.date, periods: int = 80) -> pd.DataFrame:
    trading = set(pd.bdate_range(start=start, periods=periods).date)
    end = max(trading)
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame(
        {
            "date": dates.date,
            "is_trading": [date.date() in trading for date in dates],
            "source": "fixture",
        }
    )
