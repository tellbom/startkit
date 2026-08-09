from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_strategy_api.market_data.security_master import SecurityMasterService


def _snapshot(symbol: str, name: str, listing_date: dt.date, exchange: str = "sh", status: str = "active") -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "exchange": exchange,
        "listing_date": listing_date,
        "status": status,
        "effective_date": dt.date(2026, 6, 30),
        "source": "fixture",
    }


def test_eligibility_boundaries_and_exclusions(tmp_path):
    as_of = dt.date(2026, 6, 30)
    frame = pd.DataFrame(
        [
            _snapshot("600000", "正常股", as_of - dt.timedelta(days=60)),
            _snapshot("600001", "*ST风险", as_of - dt.timedelta(days=100)),
            _snapshot("600002", "新股", as_of - dt.timedelta(days=59)),
            _snapshot("830001", "北交所", as_of - dt.timedelta(days=100), "bj"),
            _snapshot("600003", "BEST股份", as_of - dt.timedelta(days=100)),
        ]
    )
    service = SecurityMasterService(tmp_path)
    service.save_fixture(frame, as_of)
    bar = {"open": 1, "high": 2, "low": 1, "close": 2, "volume": 100}
    assert service.evaluate("600000", as_of, bar).eligible
    assert "st_stock" in service.evaluate("600001", as_of, bar).reasons
    assert "listing_age_below_minimum" in service.evaluate("600002", as_of, bar).reasons
    assert "unsupported_exchange" in service.evaluate("830001", as_of, bar).reasons
    assert service.evaluate("600003", as_of, bar).eligible
    assert "suspended" in service.evaluate("600000", as_of, {**bar, "volume": 0}).reasons


def test_incomplete_security_master_fails_closed(tmp_path):
    as_of = dt.date(2026, 6, 30)
    frame = pd.DataFrame([_snapshot("600000", "正常股", None)])
    service = SecurityMasterService(tmp_path)
    service.save_fixture(frame, as_of)
    bar = {"open": 1, "high": 2, "low": 1, "close": 2, "volume": 100}
    result = service.evaluate("600000", as_of, bar)
    assert not result.eligible
    assert result.reasons == ("security_master_incomplete",)


def test_delisting_and_missing_bar_fail_closed(tmp_path):
    as_of = dt.date(2026, 6, 30)
    service = SecurityMasterService(tmp_path)
    service.save_fixture(pd.DataFrame([_snapshot("600000", "退市整理", as_of - dt.timedelta(days=100))]), as_of)
    active_bar = {"open": 1, "high": 2, "low": 1, "close": 2, "volume": 100}
    assert "delisting_stock" in service.evaluate("600000", as_of, active_bar).reasons
    assert "missing_day_bar" in service.evaluate("600000", as_of, None).reasons
