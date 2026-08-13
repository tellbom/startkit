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
from stock_strategy_api.services.data_quality import maximum_missing_symbols, missing_symbols_within_gate
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
        missing_symbols: set[str] = set()
        pending_signals = []
        try:
            universe = self.universe.members_as_of(as_of)
            advanced_signals = self._compute_advances(strategy, as_of)
            pending_signals.extend(advanced_signals)
            stats["advanced"] = len(advanced_signals)
            for signal in advanced_signals:
                stats[f"advanced:{signal.state.value}"] += 1
                if signal.d1_confirmation:
                    stats[f"d1:{signal.d1_confirmation.value}"] += 1
            for symbol in universe.symbols:
                raw = self.ohlcv.load(symbol, "raw")
                qfq = self.ohlcv.load(symbol, "qfq")
                if raw.empty or qfq.empty:
                    missing_symbols.add(symbol)
                    continue
                day = raw.loc[raw["date"] == as_of]
                if day.empty or not (qfq["date"] == as_of).any():
                    missing_symbols.add(symbol)
                    continue
                day_bar = day.iloc[-1]
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
                    for tag in detection.signal.candidate_tags:
                        stats[f"candidate:{tag}"] += 1
                else:
                    stats["not_triggered"] += 1
                    for reason in detection.exclusion_reasons:
                        stats[f"excluded:{reason}"] += 1
            stats["data_missing"] = len(missing_symbols)
            if not missing_symbols_within_gate(len(missing_symbols), len(universe.symbols)):
                raise DataUnavailableError(
                    "scan input exceeded the missing-symbol gate",
                    details={
                        "missing_symbol_count": len(missing_symbols),
                        "missing_symbols": sorted(missing_symbols),
                        "maximum_missing_symbols": maximum_missing_symbols(len(universe.symbols)),
                        "universe_count": len(universe.symbols),
                    },
                )
            quality_warnings = [universe.warning] if universe.warning else []
            if missing_symbols:
                quality_warnings.append(
                    f"Degraded scan: {len(missing_symbols)}/{len(universe.symbols)} symbols are missing."
                )
            summary = {
                "universe_count": len(universe.symbols),
                "universe_mode": universe.mode,
                "survivorship_bias": universe.survivorship_bias,
                "universe_data_date": universe.data_date.isoformat() if universe.data_date else None,
                "universe_stale": universe.stale,
                "quality_warnings": quality_warnings,
                "missing_symbols": sorted(missing_symbols),
                "missing_symbol_ratio": round(len(missing_symbols) / len(universe.symbols), 6),
                "degraded": bool(missing_symbols),
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

    def scan_recent(
        self,
        strategy: Strategy,
        value: dt.date | str,
        *,
        lookback_trading_days: int | None = None,
    ) -> dict:
        as_of = as_date(value)
        if lookback_trading_days is None:
            config = strategy.config_snapshot()
            lookback_trading_days = (
                int(config["confirmation_days"])
                + int(config["max_entry_wait_days"])
                + int(config.get("continuation_entry_days", 0))
                + 1
            )
        days = self.calendar.trading_days_ending_on(as_of, lookback_trading_days)
        daily_runs = []
        total_triggered = 0
        total_advanced = 0
        for trade_date in days:
            result = self.scan(strategy, trade_date)
            advancement = self.advance(strategy, trade_date)
            idempotent_replay = bool(result.get("idempotent_replay", False))
            scan_triggered = 0 if idempotent_replay else int(result.get("triggered", 0))
            scan_advances = 0 if idempotent_replay else int(result.get("advanced", 0))
            total_triggered += scan_triggered
            total_advanced += scan_advances + int(advancement["updated"])
            daily_runs.append(
                {
                    "trade_date": trade_date.isoformat(),
                    "run_id": result["run_id"],
                    "triggered": scan_triggered,
                    "advanced": scan_advances + int(advancement["updated"]),
                    "idempotent_replay": idempotent_replay,
                }
            )
        return {
            "as_of": as_of.isoformat(),
            "lookback_trading_days": lookback_trading_days,
            "scanned_dates": [day.isoformat() for day in days],
            "triggered": total_triggered,
            "advanced": total_advanced,
            "daily_runs": daily_runs,
        }

    def advance(self, strategy: Strategy, value: dt.date | str) -> dict:
        as_of = as_date(value)
        if not self.calendar.is_trading_day(as_of):
            raise InvalidDateError(f"{as_of} is not a trading day")
        updates = self._compute_advances(strategy, as_of)
        self.signals.upsert_many(updates, transition_date=as_of)
        return {"as_of": as_of.isoformat(), "updated": len(updates)}

    def _compute_advances(self, strategy: Strategy, as_of: dt.date) -> list:
        updates = []
        metadata = strategy.metadata()
        for _signal_id, signal in self.signals.active(
            metadata.strategy_id,
            strategy_version=metadata.version,
            config_hash=strategy.config_hash(),
        ):
            raw = self.ohlcv.load(signal.symbol, "raw")
            next_signal = strategy.advance(signal, raw, self.calendar, as_of)
            if next_signal != signal:
                updates.append(next_signal)
        return updates
