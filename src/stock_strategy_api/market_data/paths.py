from __future__ import annotations

from pathlib import Path


def calendar_path(root: Path | str) -> Path:
    return Path(root) / "calendar" / "trading_calendar.parquet"


def universe_path(root: Path | str) -> Path:
    return Path(root) / "universe" / "csi300" / "membership.parquet"


def security_snapshot_path(root: Path | str, date: str) -> Path:
    return Path(root) / "security_master" / "snapshots" / f"{date}.parquet"


def ohlcv_path(root: Path | str, symbol: str, adjustment: str) -> Path:
    if adjustment not in {"raw", "qfq"}:
        raise ValueError(f"unsupported adjustment: {adjustment}")
    return Path(root) / "market" / adjustment / "ohlcv" / f"{symbol}.parquet"
