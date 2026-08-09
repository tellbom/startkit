from __future__ import annotations

import collections
import datetime as dt
from pathlib import Path

from stock_strategy_api.core.clock import as_date, iso_now
from stock_strategy_api.core.errors import DataUnavailableError, InvalidDateError
from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.market_data.ohlcv import OHLCVCollector
from stock_strategy_api.market_data.security_master import SecurityMasterService
from stock_strategy_api.market_data.universe import UniverseService
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.strategies.base import Strategy


class ScanService:
    def __init__(
        self,
        data_root: Path | str,
        run_repository: RunRepository,
        signal_repository: SignalRepository,
    ) -> None:
        self.calendar = CalendarService(data_root)
        self.universe = UniverseService(data_root)
        self.security_master = SecurityMasterService(data_root)
        self.ohlcv = OHLCVCollector(data_root)
        self.runs = run_repository
        self.signals = signal_repository

    def scan(self, strategy: Strategy, value: dt.date | str) -> dict:
        as_of = as_date(value)
        if not self.calendar.is_trading_day(as_of):
            raise InvalidDateError(f"{as_of} is not a trading day")
        self.runs.register_strategy(strategy)
        existing = self.runs.successful_scan_for(strategy, as_of)
        if existing:
            return {"run_id": existing["run_id"], **existing["stats"], "idempotent_replay": True}
        run_id = self.runs.start_scan(strategy, as_of)
        stats: collections.Counter[str] = collections.Counter()
        pending_signals = []
        try:
            universe = self.universe.members_as_of(as_of)
            advanced_signals = self._compute_advances(strategy, as_of)
            pending_signals.extend(advanced_signals)
            stats["advanced"] = len(advanced_signals)
            for symbol in universe.symbols:
                raw = self.ohlcv.load(symbol, "raw")
                qfq = self.ohlcv.load(symbol, "qfq")
                if raw.empty or qfq.empty:
                    stats["data_missing"] += 1
                    continue
                day = raw.loc[raw["date"] == as_of]
                day_bar = day.iloc[-1] if not day.empty else None
                eligibility = self.security_master.evaluate(
                    symbol,
                    as_of,
                    day_bar,
                    minimum_listing_days=int(strategy.config_snapshot()["minimum_listing_days"]),
                    allow_latest_snapshot=True,
                )
                if not eligibility.eligible:
                    stats["not_eligible"] += 1
                    for reason in eligibility.reasons:
                        stats[f"excluded:{reason}"] += 1
                    continue
                if not (qfq["date"] == as_of).any():
                    stats["data_missing"] += 1
                    continue
                detection = strategy.detect(
                    raw,
                    qfq,
                    as_of,
                    eligibility,
                    universe_mode=universe.mode,
                    survivorship_bias=universe.survivorship_bias,
                    calendar_source=self.calendar.source(),
                    expected_previous_trade_date=self.calendar.prev_trading_day(as_of),
                )
                if detection.triggered and detection.signal:
                    detection.signal.data_last_updated_at = iso_now()
                    pending_signals.append(detection.signal)
                    stats["triggered"] += 1
                else:
                    stats["not_triggered"] += 1
                    for reason in detection.exclusion_reasons:
                        stats[f"excluded:{reason}"] += 1
            if stats["data_missing"]:
                raise DataUnavailableError(
                    "scan input is incomplete",
                    details={"missing_symbols": stats["data_missing"], "universe_count": len(universe.symbols)},
                )
            summary = {
                "universe_count": len(universe.symbols),
                "universe_mode": universe.mode,
                "survivorship_bias": universe.survivorship_bias,
                "universe_data_date": universe.data_date.isoformat() if universe.data_date else None,
                "universe_stale": universe.stale,
                "quality_warnings": [universe.warning] if universe.warning else [],
                **dict(stats),
            }
            self.runs.commit_scan_results(
                run_id,
                pending_signals,
                transition_date=as_of,
                stats=summary,
                data_last_updated_at=iso_now(),
            )
            return {"run_id": run_id, **summary}
        except Exception as exc:
            self.runs.finish_scan(
                run_id,
                status="failed",
                stats=dict(stats),
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise

    def advance(self, strategy: Strategy, value: dt.date | str) -> dict:
        as_of = as_date(value)
        if not self.calendar.is_trading_day(as_of):
            raise InvalidDateError(f"{as_of} is not a trading day")
        updates = self._compute_advances(strategy, as_of)
        self.signals.upsert_many(updates, transition_date=as_of)
        return {"as_of": as_of.isoformat(), "updated": len(updates)}

    def _compute_advances(self, strategy: Strategy, as_of: dt.date) -> list:
        updates = []
        for _signal_id, signal in self.signals.active(strategy.metadata().strategy_id):
            raw = self.ohlcv.load(signal.symbol, "raw")
            next_signal = strategy.advance(signal, raw, self.calendar, as_of)
            if next_signal.state != signal.state or next_signal.observed_dates != signal.observed_dates:
                updates.append(next_signal)
        return updates
