from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from stock_strategy_api.core.clock import as_date
from stock_strategy_api.core.errors import DataUnavailableError, InvalidDateError
from stock_strategy_api.market_data.parquet_store import atomic_write_parquet
from stock_strategy_api.market_data.paths import calendar_path
from stock_strategy_api.market_data.retry import call_with_retry


class CalendarService:
    def __init__(self, data_root: Path | str) -> None:
        self.path = calendar_path(data_root)
        self._cache: pd.DataFrame | None = None

    def build_and_save(
        self,
        start: dt.date | str = dt.date(2010, 1, 1),
        end: dt.date | str | None = None,
    ) -> pd.DataFrame:
        start_date = as_date(start)
        end_date = as_date(end or (dt.date.today() + dt.timedelta(days=365)))
        frame = self._from_akshare(start_date, end_date)
        if frame is None:
            frame = self._from_exchange_calendar(start_date, end_date)
        atomic_write_parquet(frame, self.path)
        self._cache = frame
        return frame.copy()

    def save_fixture(self, frame: pd.DataFrame) -> None:
        required = {"date", "is_trading", "source"}
        if not required.issubset(frame.columns):
            raise ValueError(f"calendar is missing columns: {sorted(required - set(frame.columns))}")
        clean = frame.copy()
        clean["date"] = pd.to_datetime(clean["date"]).dt.date
        clean["is_trading"] = clean["is_trading"].astype(bool)
        clean = clean.sort_values("date").drop_duplicates("date", keep="last")
        atomic_write_parquet(clean, self.path)
        self._cache = clean.reset_index(drop=True)

    def is_trading_day(self, value: dt.date | str) -> bool:
        date = as_date(value)
        row = self._load().loc[lambda item: item["date"] == date]
        if row.empty:
            raise InvalidDateError(f"{date} is outside the calendar coverage")
        return bool(row.iloc[0]["is_trading"])

    def trading_days_in_range(self, start: dt.date | str, end: dt.date | str) -> list[dt.date]:
        start_date, end_date = as_date(start), as_date(end)
        frame = self._load()
        if frame.empty or start_date < frame["date"].min() or end_date > frame["date"].max():
            raise InvalidDateError(f"requested range {start_date}..{end_date} exceeds calendar coverage")
        return frame.loc[
            (frame["date"] >= start_date) & (frame["date"] <= end_date) & frame["is_trading"],
            "date",
        ].tolist()

    def trading_days_ending_on(self, value: dt.date | str, count: int) -> list[dt.date]:
        if count < 1:
            raise ValueError("count must be at least 1")
        date = as_date(value)
        if not self.is_trading_day(date):
            raise InvalidDateError(f"{date} is not a trading day")
        rows = self._load().loc[lambda item: (item["date"] <= date) & item["is_trading"]]
        if len(rows) < count:
            raise InvalidDateError(f"calendar does not contain {count} trading days ending on {date}")
        return rows.tail(count)["date"].tolist()

    def nth_trading_day_after(self, value: dt.date | str, n: int) -> dt.date:
        if n < 0:
            raise ValueError("n must not be negative")
        date = as_date(value)
        if n == 0:
            if not self.is_trading_day(date):
                raise InvalidDateError(f"{date} is not a trading day")
            return date
        rows = self._load().loc[lambda item: (item["date"] > date) & item["is_trading"]]
        if len(rows) < n:
            raise InvalidDateError(f"calendar does not contain trading day D+{n} after {date}")
        return rows.iloc[n - 1]["date"]

    def next_trading_day(self, value: dt.date | str) -> dt.date:
        return self.nth_trading_day_after(value, 1)

    def prev_trading_day(self, value: dt.date | str) -> dt.date:
        date = as_date(value)
        rows = self._load().loc[lambda item: (item["date"] < date) & item["is_trading"]]
        if rows.empty:
            raise InvalidDateError(f"calendar has no trading day before {date}")
        return rows.iloc[-1]["date"]

    def source(self) -> str:
        frame = self._load()
        return str(frame.iloc[-1]["source"]) if not frame.empty else "unknown"

    def coverage(self) -> dict:
        frame = self._load()
        return {
            "source": self.source(),
            "first_date": frame["date"].min().isoformat(),
            "last_date": frame["date"].max().isoformat(),
            "trading_days": int(frame["is_trading"].sum()),
            "accuracy_warning": self.accuracy_warning(),
        }

    def accuracy_warning(self) -> str | None:
        if self.source().startswith("exchange_calendars"):
            return (
                "Offline exchange calendar fallback; refresh from the exchange-backed provider before production use."
            )
        if self.source() == "fixture":
            return "Fixture calendar is suitable only for deterministic tests."
        return None

    def _load(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            raise DataUnavailableError(f"trading calendar is missing at {self.path}")
        frame = pd.read_parquet(self.path)
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        frame["is_trading"] = frame["is_trading"].astype(bool)
        self._cache = frame.sort_values("date").reset_index(drop=True)
        return self._cache

    @staticmethod
    def _from_akshare(start: dt.date, end: dt.date) -> pd.DataFrame | None:
        try:
            import akshare as ak

            raw = call_with_retry(ak.tool_trade_date_hist_sina, attempts=2, label="SSE calendar")
        except Exception:
            return None
        if raw is None or raw.empty:
            return None
        trading = set(pd.to_datetime(raw.iloc[:, 0]).dt.date)
        dates = pd.date_range(start, end, freq="D")
        return pd.DataFrame(
            {"date": dates.date, "is_trading": [date.date() in trading for date in dates], "source": "akshare_sina"}
        )

    @staticmethod
    def _from_exchange_calendar(start: dt.date, end: dt.date) -> pd.DataFrame:
        try:
            import exchange_calendars as exchange
        except ImportError as exc:
            raise DataUnavailableError("exchange-calendars is not installed") from exc
        calendar = exchange.get_calendar("XSHG")
        valid_start = max(start, calendar.sessions[0].date())
        valid_end = min(end, calendar.sessions[-1].date())
        if valid_start > valid_end:
            raise DataUnavailableError("requested date range is outside XSHG calendar coverage")
        sessions = {session.date() for session in calendar.sessions}
        dates = pd.date_range(valid_start, valid_end, freq="D")
        return pd.DataFrame(
            {
                "date": dates.date,
                "is_trading": [date.date() in sessions for date in dates],
                "source": "exchange_calendars_XSHG",
            }
        )
