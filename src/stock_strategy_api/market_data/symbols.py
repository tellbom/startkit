from __future__ import annotations

import re
from dataclasses import dataclass

_SIX_DIGITS = re.compile(r"^\d{6}$")


@dataclass(frozen=True, slots=True)
class MarketInfo:
    symbol: str
    prefixed: str
    exchange: str


def normalize_symbol(value: str) -> str:
    symbol = str(value).strip().lower()
    if symbol.startswith(("sh", "sz", "bj")) and len(symbol) == 8:
        symbol = symbol[2:]
    symbol = symbol.zfill(6)
    if not _SIX_DIGITS.fullmatch(symbol):
        raise ValueError(f"invalid A-share symbol: {value!r}")
    return symbol


def detect_market(value: str) -> MarketInfo:
    symbol = normalize_symbol(value)
    if symbol.startswith(("4", "8", "92")):
        return MarketInfo(symbol, f"bj{symbol}", "bj")
    if symbol.startswith(("00", "30", "20", "15", "16", "18")):
        return MarketInfo(symbol, f"sz{symbol}", "sz")
    if symbol.startswith(("60", "68", "11", "12", "5")):
        return MarketInfo(symbol, f"sh{symbol}", "sh")
    raise ValueError(f"unsupported A-share symbol prefix: {symbol}")


def is_shanghai_or_shenzhen(value: str) -> bool:
    try:
        return detect_market(value).exchange in {"sh", "sz"}
    except ValueError:
        return False
