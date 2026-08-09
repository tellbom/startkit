from __future__ import annotations

import pandas as pd

OHLCV_REQUIRED = ("symbol", "date", "open", "high", "low", "close", "volume")


def enforce_ohlcv(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    result = frame.copy()
    missing = [column for column in OHLCV_REQUIRED if column not in result.columns]
    if missing:
        raise ValueError(f"OHLCV for {symbol} is missing columns: {missing}")
    result["symbol"] = str(symbol)
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date
    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    invalid = (
        (result[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (result["high"] < result["low"])
        | (result["volume"] < 0)
    )
    if invalid.any():
        raise ValueError(f"OHLCV for {symbol} contains {int(invalid.sum())} invalid rows")
    return result.sort_values("date").drop_duplicates(subset=["symbol", "date"], keep="last").reset_index(drop=True)
