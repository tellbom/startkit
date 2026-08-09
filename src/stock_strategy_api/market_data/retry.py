from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from stock_strategy_api.core.logging import get_logger

T = TypeVar("T")
logger = get_logger(__name__)


def call_with_retry(
    function: Callable[..., T],
    *args: Any,
    attempts: int = 3,
    backoff_seconds: Sequence[float] = (0.5, 1.5, 3.0),
    label: str = "request",
    **kwargs: Any,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
            logger.warning("%s failed (%s); retrying in %.1fs", label, type(exc).__name__, delay)
            time.sleep(delay)
    assert last_error is not None
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}") from last_error
