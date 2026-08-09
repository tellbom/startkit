from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stock_strategy_api.core.clock import as_date
from stock_strategy_api.core.errors import DataUnavailableError
from stock_strategy_api.market_data.parquet_store import read_ohlcv, write_ohlcv
from stock_strategy_api.market_data.paths import ohlcv_path
from stock_strategy_api.market_data.retry import call_with_retry
from stock_strategy_api.market_data.symbols import detect_market, normalize_symbol
from stock_strategy_api.market_data.universe import UniverseService


@dataclass(frozen=True, slots=True)
class FetchResult:
    symbol: str
    adjustment: str
    success: bool
    rows_new: int
    rows_total: int
    last_date: dt.date | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    as_of: dt.date
    results: tuple[FetchResult, ...]

    @property
    def failed(self) -> tuple[FetchResult, ...]:
        return tuple(result for result in self.results if not result.success)


class OHLCVCollector:
    def __init__(self, data_root: Path | str, start_date: dt.date | str | None = None) -> None:
        self.data_root = Path(data_root)
        self.start_date = as_date(start_date or (dt.date.today() - dt.timedelta(days=365 * 3)))

    def run(self, as_of: dt.date | str) -> CollectionSummary:
        end = as_date(as_of)
        universe = UniverseService(self.data_root).members_as_of(end)
        results: list[FetchResult] = []
        for symbol in universe.symbols:
            for adjustment in ("raw", "qfq"):
                try:
                    results.append(self.collect_symbol(symbol, adjustment, end))
                except Exception as exc:
                    results.append(FetchResult(symbol, adjustment, False, 0, 0, None, str(exc)))
        return CollectionSummary(end, tuple(results))

    def collect_symbol(self, symbol: str, adjustment: str, end: dt.date) -> FetchResult:
        normalized = normalize_symbol(symbol)
        path = ohlcv_path(self.data_root, normalized, adjustment)
        existing = read_ohlcv(path)
        start = self.start_date
        if not existing.empty:
            last = max(existing["date"])
            if last >= end:
                return FetchResult(normalized, adjustment, True, 0, len(existing), last)
            start = last + dt.timedelta(days=1)
        incoming = self._fetch(normalized, adjustment, start, end)
        if incoming.empty:
            if not existing.empty:
                last = max(existing["date"])
                return FetchResult(normalized, adjustment, True, 0, len(existing), last)
            raise DataUnavailableError(f"no {adjustment} OHLCV returned for {normalized}")
        combined = pd.concat([existing, incoming], ignore_index=True)
        write_ohlcv(combined, path, normalized)
        stored = read_ohlcv(path)
        return FetchResult(normalized, adjustment, True, len(incoming), len(stored), max(stored["date"]))

    def load(self, symbol: str, adjustment: str) -> pd.DataFrame:
        return read_ohlcv(ohlcv_path(self.data_root, normalize_symbol(symbol), adjustment))

    @staticmethod
    def _fetch(symbol: str, adjustment: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataUnavailableError("akshare is not installed") from exc
        adjust_arg = "" if adjustment == "raw" else "qfq"
        market = detect_market(symbol)
        start_text, end_text = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        daily = getattr(ak, "stock_zh_a_daily", None)
        if daily is not None:
            try:
                raw = call_with_retry(
                    daily,
                    symbol=market.prefixed,
                    start_date=start_text,
                    end_date=end_text,
                    adjust=adjust_arg,
                    attempts=3,
                    label=f"{symbol} {adjustment} daily",
                )
                normalized = _normalize_ohlcv(
                    raw,
                    symbol,
                    source="sina_daily",
                    volume_multiplier=1.0,
                    infer_volume_multiplier=True,
                )
                if not normalized.empty:
                    return normalized
            except Exception:
                pass
        history = getattr(ak, "stock_zh_a_hist", None)
        if history is None:
            raise DataUnavailableError("no A-share OHLCV provider is available")
        raw = call_with_retry(
            history,
            symbol=symbol,
            period="daily",
            start_date=start_text,
            end_date=end_text,
            adjust=adjust_arg,
            attempts=3,
            label=f"{symbol} {adjustment} hist",
        )
        return _normalize_ohlcv(
            raw,
            symbol,
            source="eastmoney_hist",
            volume_multiplier=100.0,
            infer_volume_multiplier=True,
        )


def _normalize_ohlcv(
    frame: pd.DataFrame | None,
    symbol: str,
    *,
    source: str = "unknown",
    volume_multiplier: float = 1.0,
    infer_volume_multiplier: bool = False,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
    result = (
        frame.reset_index() if frame.index.name == "date" or isinstance(frame.index, pd.DatetimeIndex) else frame.copy()
    )
    result = result.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }
    )
    result["symbol"] = symbol
    raw_volume = pd.to_numeric(result["volume"], errors="coerce")
    if infer_volume_multiplier and "amount" in result:
        amount = pd.to_numeric(result["amount"], errors="coerce")
        close = pd.to_numeric(result["close"], errors="coerce")
        unit_ratio = (amount / (close * raw_volume)).replace([float("inf"), float("-inf")], pd.NA).dropna()
        if not unit_ratio.empty:
            median_ratio = float(unit_ratio.median())
            if 20 <= median_ratio <= 200:
                volume_multiplier = 100.0
            elif 0.2 <= median_ratio <= 5:
                volume_multiplier = 1.0
    result["volume"] = raw_volume * volume_multiplier
    result["source"] = source
    columns = [
        column
        for column in ("symbol", "date", "open", "high", "low", "close", "volume", "amount", "source")
        if column in result
    ]
    return result[columns]
