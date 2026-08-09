from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path

import pandas as pd

from stock_strategy_api.market_data.schemas import enforce_ohlcv


def atomic_write_parquet(frame: pd.DataFrame, path: Path | str) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output = frame.copy()
    for column in output.columns:
        if output[column].dtype == object:
            sample = output[column].dropna()
            if not sample.empty and isinstance(sample.iloc[0], dt.date):
                output[column] = output[column].map(lambda value: value.isoformat() if value else None)
    descriptor, temporary = tempfile.mkstemp(dir=output_path.parent, suffix=".parquet.tmp")
    os.close(descriptor)
    try:
        output.to_parquet(temporary, index=False)
        os.replace(temporary, output_path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return output_path


def write_ohlcv(frame: pd.DataFrame, path: Path | str, symbol: str) -> Path:
    return atomic_write_parquet(enforce_ohlcv(frame, symbol), path)


def read_ohlcv(path: Path | str) -> pd.DataFrame:
    source = Path(path)
    if not source.exists():
        return pd.DataFrame(columns=list(OHLCV_EMPTY_COLUMNS))
    frame = pd.read_parquet(source)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame.sort_values("date").reset_index(drop=True)


OHLCV_EMPTY_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "amount")
