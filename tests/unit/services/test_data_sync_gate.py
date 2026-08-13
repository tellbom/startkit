from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from stock_strategy_api.core.errors import DataUnavailableError
from stock_strategy_api.services.data_sync import DataSyncService


class _Calendar:
    def build_and_save(self, **_kwargs):
        return None

    def is_trading_day(self, _as_of):
        return True

    def source(self):
        return "fixture"

    def accuracy_warning(self):
        return None


class _Universe:
    symbols = tuple(f"{index:06d}" for index in range(300))

    def fetch_and_save(self, _as_of):
        return None

    def members_as_of(self, _as_of):
        return SimpleNamespace(symbols=self.symbols)


class _SecurityMaster:
    def fetch_and_save(self, _as_of):
        return range(300)


def _service(failed_symbols: list[str], *, duplicate_adjustments: bool = False) -> DataSyncService:
    failures = [SimpleNamespace(symbol=symbol, adjustment="raw", success=False, error="failed") for symbol in failed_symbols]
    if duplicate_adjustments:
        failures.extend(
            SimpleNamespace(symbol=symbol, adjustment="qfq", success=False, error="failed")
            for symbol in failed_symbols
        )
    failure_ids = {(item.symbol, item.adjustment) for item in failures}
    results = [
        SimpleNamespace(symbol=symbol, adjustment=adjustment, success=(symbol, adjustment) not in failure_ids)
        for symbol in _Universe.symbols
        for adjustment in ("raw", "qfq")
    ]
    service = DataSyncService.__new__(DataSyncService)
    service.calendar = _Calendar()
    service.universe = _Universe()
    service.security_master = _SecurityMaster()
    service.ohlcv = SimpleNamespace(run=lambda _as_of: SimpleNamespace(results=tuple(results), failed=tuple(failures)))
    return service


def test_sync_allows_up_to_fifteen_unique_missing_symbols():
    summary = _service(list(_Universe.symbols[:15]), duplicate_adjustments=True).sync(dt.date(2026, 8, 13))

    assert summary.degraded is True
    assert len(summary.missing_symbols) == 15
    assert summary.missing_symbol_ratio == 0.05


def test_sync_rejects_sixteen_unique_missing_symbols():
    with pytest.raises(DataUnavailableError, match="exceeded the missing-symbol gate"):
        _service(list(_Universe.symbols[:16])).sync(dt.date(2026, 8, 13))
