from __future__ import annotations

import math

MAX_MISSING_SYMBOL_RATIO = 0.05


def maximum_missing_symbols(universe_count: int) -> int:
    if universe_count < 1:
        return 0
    return math.floor(universe_count * MAX_MISSING_SYMBOL_RATIO)


def missing_symbols_within_gate(missing_count: int, universe_count: int) -> bool:
    return missing_count <= maximum_missing_symbols(universe_count)
