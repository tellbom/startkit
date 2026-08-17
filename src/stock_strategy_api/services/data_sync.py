from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from pathlib import Path

from stock_strategy_api.core.clock import as_date, iso_now
from stock_strategy_api.core.errors import DataUnavailableError, InvalidDateError
from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.market_data.ohlcv import OHLCVCollector
from stock_strategy_api.market_data.security_master import SecurityMasterService
from stock_strategy_api.market_data.universe import UniverseService
from stock_strategy_api.services.data_quality import maximum_missing_symbols, missing_symbols_within_gate


@dataclass(frozen=True, slots=True)
class DataSyncSummary:
    as_of: dt.date
    universe_count: int
    ohlcv_succeeded: int
    ohlcv_failed: int
    ohlcv_updated: int
    ohlcv_up_to_date: int
    ohlcv_rows_new: int
    missing_symbols: tuple[str, ...]
    missing_symbol_ratio: float
    degraded: bool
    security_rows: int
    calendar_source: str
    calendar_accuracy_warning: str | None
    finished_at: str

    def to_dict(self) -> dict:
        result = asdict(self)
        result["as_of"] = self.as_of.isoformat()
        result["missing_symbols"] = list(self.missing_symbols)
        return result


class DataSyncService:
    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root)
        self.calendar = CalendarService(data_root)
        self.universe = UniverseService(data_root)
        self.security_master = SecurityMasterService(data_root)
        self.ohlcv = OHLCVCollector(data_root)

    def sync(self, value: dt.date | str) -> DataSyncSummary:
        as_of = as_date(value)
        self.calendar.build_and_save(end=as_of + dt.timedelta(days=365))
        if not self.calendar.is_trading_day(as_of):
            raise InvalidDateError(f"{as_of} is not a trading day")
        self.universe.fetch_and_save(as_of)
        snapshot = self.universe.members_as_of(as_of)
        if len(snapshot.symbols) != 300:
            raise DataUnavailableError(
                "CSI 300 membership count is not 300",
                details={"actual_count": len(snapshot.symbols)},
            )
        security = self.security_master.fetch_and_save(as_of)
        collection = self.ohlcv.run(as_of)
        succeeded = sum(1 for result in collection.results if result.success)
        updated = sum(1 for result in collection.results if result.success and getattr(result, "rows_new", 0) > 0)
        up_to_date = sum(1 for result in collection.results if result.success and getattr(result, "rows_new", 0) == 0)
        rows_new = sum(getattr(result, "rows_new", 0) for result in collection.results if result.success)
        missing_symbols = tuple(sorted({result.symbol for result in collection.failed}))
        summary = DataSyncSummary(
            as_of=as_of,
            universe_count=len(snapshot.symbols),
            ohlcv_succeeded=succeeded,
            ohlcv_failed=len(collection.failed),
            ohlcv_updated=updated,
            ohlcv_up_to_date=up_to_date,
            ohlcv_rows_new=rows_new,
            missing_symbols=missing_symbols,
            missing_symbol_ratio=round(len(missing_symbols) / len(snapshot.symbols), 6),
            degraded=bool(missing_symbols),
            security_rows=len(security),
            calendar_source=self.calendar.source(),
            calendar_accuracy_warning=self.calendar.accuracy_warning(),
            finished_at=iso_now(),
        )
        if not missing_symbols_within_gate(len(missing_symbols), len(snapshot.symbols)):
            raise DataUnavailableError(
                "OHLCV synchronization exceeded the missing-symbol gate",
                details={
                    **summary.to_dict(),
                    "maximum_missing_symbols": maximum_missing_symbols(len(snapshot.symbols)),
                    "failed": [
                        {"symbol": result.symbol, "adjustment": result.adjustment, "error": result.error}
                        for result in collection.failed
                    ],
                },
            )
        return summary
