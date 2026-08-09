from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> dt.datetime:
    return dt.datetime.now(tz=BUSINESS_TIMEZONE)


def as_date(value: dt.date | dt.datetime | str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value)


def iso_now() -> str:
    return now_shanghai().isoformat(timespec="seconds")
