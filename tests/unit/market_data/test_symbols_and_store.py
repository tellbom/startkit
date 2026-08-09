from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_strategy_api.market_data.ohlcv import OHLCVCollector, _normalize_ohlcv
from stock_strategy_api.market_data.parquet_store import atomic_write_parquet, read_ohlcv, write_ohlcv
from stock_strategy_api.market_data.paths import ohlcv_path
from stock_strategy_api.market_data.symbols import detect_market, normalize_symbol


def test_symbol_normalization_and_markets():
    assert normalize_symbol("sh600000") == "600000"
    assert detect_market("300750").exchange == "sz"
    assert detect_market("688001").exchange == "sh"
    assert detect_market("830001").exchange == "bj"
    with pytest.raises(ValueError):
        normalize_symbol("bad")


def test_ohlcv_store_enforces_and_deduplicates(tmp_path):
    frame = pd.DataFrame(
        [
            {"symbol": "x", "date": "2026-01-02", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10},
            {"symbol": "x", "date": "2026-01-02", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 20},
        ]
    )
    path = tmp_path / "ohlcv.parquet"
    write_ohlcv(frame, path, "600000")
    stored = read_ohlcv(path)
    assert len(stored) == 1
    assert stored.iloc[0]["close"] == 3
    assert stored.iloc[0]["symbol"] == "600000"


def test_ohlcv_store_fails_on_missing_columns(tmp_path):
    with pytest.raises(ValueError, match="missing columns"):
        write_ohlcv(pd.DataFrame({"date": ["2026-01-01"]}), tmp_path / "bad.parquet", "600000")


def test_eastmoney_lot_volume_is_normalized_to_shares():
    raw = pd.DataFrame([{"日期": "2026-01-02", "开盘": 1, "最高": 2, "最低": 1, "收盘": 2, "成交量": 123}])
    normalized = _normalize_ohlcv(raw, "600000", source="eastmoney_hist", volume_multiplier=100)
    assert normalized.iloc[0]["volume"] == 12_300
    assert normalized.iloc[0]["source"] == "eastmoney_hist"


def test_volume_unit_is_inferred_from_turnover_amount():
    raw = pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "open": 9.2,
                "high": 9.3,
                "low": 9.1,
                "close": 9.2,
                "volume": 565_457,
                "amount": 520_000_000,
            }
        ]
    )
    normalized = _normalize_ohlcv(
        raw,
        "600000",
        source="sina_daily",
        volume_multiplier=1,
        infer_volume_multiplier=True,
    )
    assert normalized.iloc[0]["volume"] == 56_545_700

    already_shares = raw.copy()
    already_shares["volume"] = 52_000_000
    normalized_shares = _normalize_ohlcv(
        already_shares,
        "600000",
        source="eastmoney_hist",
        volume_multiplier=100,
        infer_volume_multiplier=True,
    )
    assert normalized_shares.iloc[0]["volume"] == 52_000_000


def test_atomic_write_failure_preserves_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.parquet"
    atomic_write_parquet(pd.DataFrame({"value": [1]}), path)
    original = path.read_bytes()

    def fail_write(*_args, **_kwargs):
        raise OSError("injected write failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail_write)
    with pytest.raises(OSError, match="injected"):
        atomic_write_parquet(pd.DataFrame({"value": [2]}), path)
    assert path.read_bytes() == original


def test_incremental_fetch_failure_keeps_previous_ohlcv(tmp_path, monkeypatch):
    path = ohlcv_path(tmp_path, "600000", "raw")
    existing = pd.DataFrame(
        [
            {
                "symbol": "600000",
                "date": dt.date(2026, 1, 2),
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "volume": 10,
            }
        ]
    )
    write_ohlcv(existing, path, "600000")
    original = path.read_bytes()

    def fail_fetch(*_args, **_kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(OHLCVCollector, "_fetch", staticmethod(fail_fetch))
    with pytest.raises(ConnectionError, match="network down"):
        OHLCVCollector(tmp_path).collect_symbol("600000", "raw", dt.date(2026, 1, 5))
    assert path.read_bytes() == original


def test_up_to_date_collection_skips_network_and_adjustment_paths_are_separate(tmp_path, monkeypatch):
    end = dt.date(2026, 1, 2)
    frame = pd.DataFrame([{"symbol": "600000", "date": end, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}])
    raw_path = ohlcv_path(tmp_path, "600000", "raw")
    qfq_path = ohlcv_path(tmp_path, "600000", "qfq")
    assert raw_path != qfq_path
    write_ohlcv(frame, raw_path, "600000")

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("network should not be called")

    monkeypatch.setattr(OHLCVCollector, "_fetch", staticmethod(unexpected_fetch))
    result = OHLCVCollector(tmp_path).collect_symbol("600000", "raw", end)
    assert result.rows_new == 0
    assert result.last_date == end
