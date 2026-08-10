from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stock_strategy_api.core.clock import as_date
from stock_strategy_api.core.config import Settings
from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.market_data.ohlcv import OHLCVCollector
from stock_strategy_api.market_data.security_master import SecurityMasterService
from stock_strategy_api.market_data.universe import UniverseService
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.strategies.base import SignalState, Strategy, StrategySignal


@dataclass(frozen=True, slots=True)
class CostConfig:
    commission_bps: float = 3.0
    buy_slippage_bps: float = 5.0
    sell_slippage_bps: float = 5.0
    stamp_duty_bps: float = 5.0

    @classmethod
    def from_settings(cls, settings: Settings) -> CostConfig:
        return cls(
            commission_bps=settings.commission_bps,
            buy_slippage_bps=settings.buy_slippage_bps,
            sell_slippage_bps=settings.sell_slippage_bps,
            stamp_duty_bps=settings.stamp_duty_bps,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "commission_bps": self.commission_bps,
            "buy_slippage_bps": self.buy_slippage_bps,
            "sell_slippage_bps": self.sell_slippage_bps,
            "stamp_duty_bps": self.stamp_duty_bps,
        }


class BacktestService:
    def __init__(
        self,
        data_root: Path | str,
        runs: RunRepository,
        *,
        costs: CostConfig | None = None,
    ) -> None:
        self.calendar = CalendarService(data_root)
        self.universe = UniverseService(data_root)
        self.security_master = SecurityMasterService(data_root)
        self.ohlcv = OHLCVCollector(data_root)
        self.runs = runs
        self.costs = costs or CostConfig()

    def run(self, strategy: Strategy, start: dt.date | str, end: dt.date | str) -> dict:
        start_date, end_date = as_date(start), as_date(end)
        if start_date > end_date:
            raise ValueError("backtest start must not be after end")
        days = self.calendar.trading_days_in_range(start_date, end_date)
        end_universe = self.universe.members_as_of(end_date)
        _, end_security_pit = self.security_master.load_snapshot(end_date, allow_latest=True)
        parameters = {
            "strategy_config": strategy.config_snapshot(),
            "costs": self.costs.to_dict(),
            "entry_rule": (
                "first tradable open from D2; "
                f"maximum {strategy.config_snapshot()['max_entry_wait_days']} trading day(s)"
            ),
            "exit_rule": (
                "next tradable open after full fill subject to T+1; otherwise "
                f"D+{strategy.config_snapshot()['max_holding_days']} close"
            ),
        }
        run_id = self.runs.create_backtest(
            strategy,
            start_date,
            end_date,
            universe_mode=end_universe.mode,
            survivorship_bias=end_universe.survivorship_bias,
            security_master_pit=end_security_pit,
            parameters=parameters,
        )
        events: list[dict[str, Any]] = []
        counts = {
            "candidates": 0,
            "entry_eligible": 0,
            "weak_d1": 0,
            "invalidated": 0,
            "indeterminate": 0,
            "unfilled_entry": 0,
            "invalidated_before_entry": 0,
        }
        all_security_pit = end_security_pit
        security_pit_days = 0
        security_pit_missing_dates: list[str] = []
        try:
            market_cache: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
            universe_modes: set[str] = set()
            survivorship_bias = False
            for signal_date in days:
                day_universe = self.universe.members_as_of(signal_date)
                universe_modes.add(day_universe.mode)
                survivorship_bias = survivorship_bias or day_universe.survivorship_bias
                _, day_security_pit = self.security_master.load_snapshot(signal_date, allow_latest=True)
                security_pit_days += int(day_security_pit)
                all_security_pit = all_security_pit and day_security_pit
                if not day_security_pit:
                    security_pit_missing_dates.append(signal_date.isoformat())
                for symbol in day_universe.symbols:
                    if symbol not in market_cache:
                        market_cache[symbol] = (self.ohlcv.load(symbol, "raw"), self.ohlcv.load(symbol, "qfq"))
                    raw, qfq = market_cache[symbol]
                    if raw.empty or qfq.empty:
                        continue
                    raw_day = raw.loc[raw["date"] == signal_date]
                    if raw_day.empty:
                        continue
                    eligibility = self.security_master.evaluate(
                        symbol,
                        signal_date,
                        raw_day.iloc[-1],
                        minimum_listing_days=int(strategy.config_snapshot()["minimum_listing_days"]),
                        allow_latest_snapshot=True,
                    )
                    all_security_pit = all_security_pit and eligibility.security_master_pit
                    detection = strategy.detect(
                        raw,
                        qfq,
                        signal_date,
                        eligibility,
                        universe_mode=day_universe.mode,
                        survivorship_bias=day_universe.survivorship_bias,
                        calendar_source=self.calendar.source(),
                        expected_previous_trade_date=self.calendar.prev_trading_day(signal_date),
                    )
                    if not detection.triggered or not detection.signal:
                        continue
                    counts["candidates"] += 1
                    confirmation_days = [self.calendar.next_trading_day(signal_date)]
                    confirmation_cutoff = confirmation_days[0]
                    if confirmation_cutoff > end_date:
                        continue
                    signal = detection.signal
                    state_path = [{"date": signal_date.isoformat(), "state": signal.state.value}]
                    for confirmation_day in confirmation_days:
                        signal = strategy.advance(signal, raw, self.calendar, confirmation_day)
                        state_path.append({"date": confirmation_day.isoformat(), "state": signal.state.value})
                        if signal.state in {SignalState.INVALIDATED, SignalState.INDETERMINATE}:
                            break
                    if signal.state == SignalState.INVALIDATED:
                        counts["invalidated"] += 1
                        continue
                    if signal.state == SignalState.INDETERMINATE:
                        counts["indeterminate"] += 1
                        continue
                    if signal.state == SignalState.WEAK_D1:
                        counts["weak_d1"] += 1
                        continue
                    if signal.state != SignalState.ENTRY_ELIGIBLE:
                        continue
                    counts["entry_eligible"] += 1
                    event = self._build_event(signal, raw, qfq, strategy.config_snapshot(), state_path)
                    if event["status"] == "unfilled_entry":
                        counts["unfilled_entry"] += 1
                    elif event["status"] == "invalidated_before_entry":
                        counts["invalidated_before_entry"] += 1
                    events.append(event)
            metrics = self._metrics(events, counts)
            final_mode = "point_in_time" if universe_modes == {"point_in_time"} else "current_snapshot"
            metrics.update(
                {
                    "universe_mode": final_mode,
                    "survivorship_bias": survivorship_bias,
                    "security_master_pit": all_security_pit,
                    "security_master_pit_days": security_pit_days,
                    "security_master_total_days": len(days),
                    "security_master_pit_coverage": security_pit_days / len(days) if days else 0.0,
                    "security_master_pit_missing_dates": security_pit_missing_dates,
                    "production_verified": (
                        final_mode == "point_in_time" and not survivorship_bias and all_security_pit
                    ),
                }
            )
            event_quality = {
                "universe_mode": final_mode,
                "survivorship_bias": survivorship_bias,
                "security_master_pit": all_security_pit,
                "security_master_pit_coverage": metrics["security_master_pit_coverage"],
                "security_master_pit_missing_dates": security_pit_missing_dates,
                "production_verified": metrics["production_verified"],
            }
            for event in events:
                event["backtest_quality"] = event_quality
                self.runs.add_backtest_event(run_id, event)
            self.runs.finish_backtest(run_id, "success", metrics)
            return {"run_id": run_id, "metrics": metrics}
        except Exception as exc:
            self.runs.finish_backtest(
                run_id,
                "failed",
                self._metrics(events, counts),
                {"type": type(exc).__name__, "message": str(exc)},
            )
            raise

    def _build_event(
        self,
        signal: StrategySignal,
        raw: pd.DataFrame,
        qfq: pd.DataFrame,
        strategy_config: dict,
        state_path: list[dict[str, str]],
    ) -> dict[str, Any]:
        max_wait = int(strategy_config["max_entry_wait_days"])
        entry_date: dt.date | None = None
        entry_delay_trading_days: int | None = None
        invalidated_before_entry_date: dt.date | None = None
        for offset in range(1, max_wait + 1):
            candidate = self.calendar.nth_trading_day_after(signal.confirmation_date, offset)  # type: ignore[arg-type]
            row = _bar(raw, candidate)
            if row is None or float(row["volume"]) <= 0:
                continue
            previous = _previous_bar(raw, candidate)
            one_price_up = (
                float(row["high"]) == float(row["low"])
                and previous is not None
                and float(row["open"]) > float(previous["close"])
            )
            if one_price_up:
                continue
            if float(row["open"]) <= signal.gap_floor:
                invalidated_before_entry_date = candidate
                break
            entry_date = candidate
            entry_delay_trading_days = offset
            break
        base = {
            "symbol": signal.symbol,
            "signal_date": signal.signal_date.isoformat(),
            "confirmation_date": signal.confirmation_date.isoformat() if signal.confirmation_date else None,
            "phase": signal.phase.value,
            "rule_score": signal.rule_score,
            "d0_score": signal.d0_score,
            "d1_score": signal.d1_score,
            "candidate_tags": signal.candidate_tags,
            "d1_confirmation": signal.d1_confirmation.value if signal.d1_confirmation else None,
            "gap_floor": signal.gap_floor,
            "gap_top": signal.gap_top if signal.gap_top is not None else signal.gap_ceiling,
            "strategy_version": signal.strategy_version,
            "config_hash": signal.config_hash,
            "state_path": state_path,
            "observed_dates": [value.isoformat() for value in signal.observed_dates],
            "universe_mode": signal.universe_mode,
            "survivorship_bias": signal.survivorship_bias,
            "security_master_pit": signal.security_master_pit,
        }
        if entry_date is None:
            if invalidated_before_entry_date is not None:
                return {
                    **base,
                    "status": "invalidated_before_entry",
                    "entry_date": None,
                    "exit_date": None,
                    "invalidated_date": invalidated_before_entry_date.isoformat(),
                    "exit_reason": "gap_destroyed_at_entry_open",
                }
            return {**base, "status": "unfilled_entry", "entry_date": None, "exit_date": None}

        entry_qfq = _bar(qfq, entry_date)
        if entry_qfq is None:
            return {**base, "status": "data_incomplete", "entry_date": entry_date.isoformat(), "exit_date": None}
        entry_price = float(entry_qfq["open"])
        horizons: dict[str, dict[str, float | None]] = {}
        for horizon in strategy_config["backtest_horizons"]:
            horizon_date = self.calendar.nth_trading_day_after(entry_date, int(horizon))
            horizon_bar = _bar(qfq, horizon_date)
            if horizon_bar is None:
                horizons[str(horizon)] = {
                    "gross_return": None,
                    "net_return": None,
                    "mfe": None,
                    "mae": None,
                }
            else:
                horizon_price = float(horizon_bar["close"])
                mfe, mae = _excursions(qfq, entry_date, horizon_date, entry_price)
                horizons[str(horizon)] = {
                    "gross_return": horizon_price / entry_price - 1,
                    "net_return": self._net_return(entry_price, horizon_price),
                    "mfe": mfe,
                    "mae": mae,
                }

        last_horizon = int(strategy_config["max_holding_days"])
        planned_exit = self.calendar.nth_trading_day_after(entry_date, last_horizon)
        exit_date = planned_exit
        exit_field = "close"
        exit_reason = f"fixed_{last_horizon}d"
        for offset in range(0, last_horizon + 1):
            observed_date = self.calendar.nth_trading_day_after(entry_date, offset)
            observed = _bar(raw, observed_date)
            if observed is not None and float(observed["low"]) <= signal.gap_floor:
                first_allowed_exit = max(observed_date, entry_date)
                tradable_exit = _next_tradable_bar_date(raw, qfq, first_allowed_exit)
                if tradable_exit is None:
                    return {
                        **base,
                        "status": "data_incomplete",
                        "entry_date": entry_date.isoformat(),
                        "exit_date": None,
                        "horizon_returns": horizons,
                        "exit_reason": "full_fill_without_later_tradable_bar",
                    }
                exit_date = tradable_exit
                exit_field = "open"
                exit_reason = "full_fill_next_open"
                break
        exit_qfq = _bar(qfq, exit_date)
        if exit_qfq is None:
            return {
                **base,
                "status": "data_incomplete",
                "entry_date": entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "horizon_returns": horizons,
            }
        exit_price = float(exit_qfq[exit_field])
        gross_return = exit_price / entry_price - 1
        net_return = self._net_return(entry_price, exit_price)
        mfe, mae = _excursions(qfq, entry_date, exit_date, entry_price)
        post_entry_path = _post_entry_state_path(raw, signal, self.calendar, entry_date, last_horizon)
        return {
            **base,
            "status": "filled",
            "entry_date": entry_date.isoformat(),
            "entry_delay_trading_days": entry_delay_trading_days,
            "entry_price_qfq": entry_price,
            "exit_date": exit_date.isoformat(),
            "exit_price_qfq": exit_price,
            "exit_reason": exit_reason,
            "gross_return": gross_return,
            "net_return": net_return,
            "mfe": mfe,
            "mae": mae,
            "gap_filled_after_entry": exit_reason == "full_fill_next_open",
            "post_entry_state_path": post_entry_path,
            "horizon_returns": horizons,
            "costs": self.costs.to_dict(),
            "cost_breakdown": self._cost_breakdown(entry_price, exit_price),
        }

    def _net_return(self, entry_price: float, exit_price: float) -> float:
        commission = self.costs.commission_bps / 10_000
        buy_cost = entry_price * (1 + self.costs.buy_slippage_bps / 10_000) * (1 + commission)
        sell_proceeds = (
            exit_price
            * (1 - self.costs.sell_slippage_bps / 10_000)
            * (1 - commission - self.costs.stamp_duty_bps / 10_000)
        )
        return sell_proceeds / buy_cost - 1

    def _cost_breakdown(self, entry_price: float, exit_price: float) -> dict[str, float]:
        gross_return = exit_price / entry_price - 1
        net_return = self._net_return(entry_price, exit_price)
        return {
            "buy_slippage_rate": self.costs.buy_slippage_bps / 10_000,
            "sell_slippage_rate": self.costs.sell_slippage_bps / 10_000,
            "buy_commission_rate": self.costs.commission_bps / 10_000,
            "sell_commission_rate": self.costs.commission_bps / 10_000,
            "stamp_duty_rate": self.costs.stamp_duty_bps / 10_000,
            "total_return_drag": gross_return - net_return,
        }

    @staticmethod
    def _metrics(events: list[dict[str, Any]], counts: dict[str, int]) -> dict[str, Any]:
        filled = [event for event in events if event["status"] == "filled"]
        net = pd.Series([event["net_return"] for event in filled], dtype=float)
        gross_returns = pd.Series([event["gross_return"] for event in filled], dtype=float)
        mfe = pd.Series([event["mfe"] for event in filled], dtype=float)
        mae = pd.Series([event["mae"] for event in filled], dtype=float)
        horizon_keys = sorted({key for event in filled for key in event.get("horizon_returns", {})}, key=int)
        horizons = {}
        for key in horizon_keys:
            horizon_gross = pd.Series(
                [
                    event["horizon_returns"][key]["gross_return"]
                    for event in filled
                    if event["horizon_returns"][key]["gross_return"] is not None
                ],
                dtype=float,
            )
            net_horizon = pd.Series(
                [
                    event["horizon_returns"][key]["net_return"]
                    for event in filled
                    if event["horizon_returns"][key]["net_return"] is not None
                ],
                dtype=float,
            )
            horizon_mfe = pd.Series(
                [
                    event["horizon_returns"][key]["mfe"]
                    for event in filled
                    if event["horizon_returns"][key]["mfe"] is not None
                ],
                dtype=float,
            )
            horizon_mae = pd.Series(
                [
                    event["horizon_returns"][key]["mae"]
                    for event in filled
                    if event["horizon_returns"][key]["mae"] is not None
                ],
                dtype=float,
            )
            horizons[key] = {
                "gross": _series_metrics(horizon_gross),
                "net": _series_metrics(net_horizon),
                "mfe": _series_metrics(horizon_mfe),
                "mae": _series_metrics(horizon_mae),
            }
        phase_metrics = {}
        for phase in sorted({event["phase"] for event in events}):
            phase_net = pd.Series([event["net_return"] for event in filled if event["phase"] == phase], dtype=float)
            phase_metrics[phase] = _series_metrics(phase_net)
        observed_candidates = (
            counts["entry_eligible"] + counts["weak_d1"] + counts["invalidated"] + counts["indeterminate"]
        )
        return {
            **counts,
            "events": len(events),
            "filled_entries": len(filled),
            "gross_return": _series_metrics(gross_returns),
            "net_return": _series_metrics(net),
            "mfe": _series_metrics(mfe),
            "mae": _series_metrics(mae),
            "horizon_returns": horizons,
            "full_fill_rate_d1": (counts["invalidated"] / observed_candidates if observed_candidates else None),
            "full_fill_rate_after_entry": (
                sum(bool(event.get("gap_filled_after_entry")) for event in filled) / len(filled) if filled else None
            ),
            "phase_metrics": phase_metrics,
            "phase_counts": (
                {
                    str(key): int(value)
                    for key, value in pd.Series([event["phase"] for event in events]).value_counts().items()
                }
                if events
                else {}
            ),
            "candidate_tag_metrics": {
                tag: _series_metrics(
                    pd.Series(
                        [event["net_return"] for event in filled if tag in event.get("candidate_tags", [])],
                        dtype=float,
                    )
                )
                for tag in ("SHORT_GAP", "STRICT_GAP")
            },
            "d1_confirmation_metrics": {
                confirmation: _series_metrics(
                    pd.Series(
                        [event["net_return"] for event in filled if event.get("d1_confirmation") == confirmation],
                        dtype=float,
                    )
                )
                for confirmation in ("fully_unfilled", "partial_reclaimed")
            },
            "entry_delay_metrics": {
                str(delay): _series_metrics(
                    pd.Series(
                        [event["net_return"] for event in filled if event.get("entry_delay_trading_days") == delay],
                        dtype=float,
                    )
                )
                for delay in (1, 2)
            },
        }


def _series_metrics(series: pd.Series) -> dict[str, float | int | None]:
    if series.empty:
        return {"sample_size": 0, "mean": None, "median": None, "win_rate": None, "p10": None, "p90": None}
    return {
        "sample_size": int(len(series)),
        "mean": float(series.mean()),
        "median": float(series.median()),
        "win_rate": float((series > 0).mean()),
        "p10": float(series.quantile(0.10)),
        "p90": float(series.quantile(0.90)),
    }


def _bar(frame: pd.DataFrame, date: dt.date) -> pd.Series | None:
    row = frame.loc[frame["date"] == date]
    return row.iloc[-1] if not row.empty else None


def _previous_bar(frame: pd.DataFrame, date: dt.date) -> pd.Series | None:
    rows = frame.loc[frame["date"] < date].sort_values("date")
    return rows.iloc[-1] if not rows.empty else None


def _next_tradable_bar_date(raw: pd.DataFrame, qfq: pd.DataFrame, after: dt.date) -> dt.date | None:
    rows = raw.loc[(raw["date"] > after) & (raw["volume"] > 0)].sort_values("date")
    qfq_dates = set(qfq["date"])
    for value in rows["date"]:
        if value in qfq_dates:
            return value
    return None


def _excursions(
    frame: pd.DataFrame, start: dt.date, end: dt.date, entry_price: float
) -> tuple[float | None, float | None]:
    rows = frame.loc[(frame["date"] >= start) & (frame["date"] <= end)]
    if rows.empty or entry_price <= 0:
        return None, None
    return float(rows["high"].max()) / entry_price - 1, float(rows["low"].min()) / entry_price - 1


def _post_entry_state_path(
    raw: pd.DataFrame,
    signal: StrategySignal,
    calendar: CalendarService,
    entry_date: dt.date,
    max_holding_days: int,
) -> list[dict[str, str]]:
    gap_top = float(signal.gap_top if signal.gap_top is not None else signal.gap_ceiling)
    path: list[dict[str, str]] = []
    for offset in range(max_holding_days + 1):
        day = calendar.nth_trading_day_after(entry_date, offset)
        row = _bar(raw, day)
        if row is None or float(row["volume"]) <= 0:
            state = "untradable"
        elif float(row["low"]) <= signal.gap_floor:
            state = "gap_fully_filled"
        elif float(row["low"]) < gap_top:
            state = "gap_partially_filled"
        else:
            state = "gap_fully_held"
        path.append({"date": day.isoformat(), "state": state})
        if state == "gap_fully_filled":
            break
    return path
