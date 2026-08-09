from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stock_strategy_api.core.config import get_settings
from stock_strategy_api.core.errors import DomainError
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.services.backtest_service import BacktestService, CostConfig
from stock_strategy_api.services.data_sync import DataSyncService
from stock_strategy_api.services.scan_service import ScanService
from stock_strategy_api.strategies.registry import get_registry
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapConfig, StrongGapUpStrategy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-strategy")
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync-data", help="Synchronize calendar, CSI300, security and OHLCV data")
    sync.add_argument("--as-of", required=True)

    scan = commands.add_parser("scan", help="Backfill recent trading days and advance signals through D3")
    scan.add_argument("--strategy", required=True)
    scan.add_argument("--as-of", required=True)
    scan.add_argument(
        "--lookback-trading-days",
        type=int,
        default=None,
        help=(
            "Number of D0 trading dates to scan chronologically; default covers the strategy's "
            "confirmation and entry-wait lifecycle; use 1 for single-day mode"
        ),
    )

    advance = commands.add_parser("advance-signals", help="Advance D1-D3 signal lifecycle")
    advance.add_argument("--strategy", default="strong_gap_up_v1")
    advance.add_argument("--as-of", required=True)

    backtest = commands.add_parser("backtest", help="Run event-driven T+1 backtest")
    backtest.add_argument("--strategy", required=True)
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--config", help="JSON file containing a complete or partial strategy configuration")

    show = commands.add_parser("show-run", help="Show a scan or backtest run")
    show.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize()
    runs = RunRepository(database)
    signals = SignalRepository(database)
    registry = get_registry()
    try:
        if args.command == "sync-data":
            result = DataSyncService(settings.data_dir).sync(args.as_of).to_dict()
        elif args.command == "scan":
            result = ScanService(settings.data_dir, runs, signals).scan_recent(
                registry.get(args.strategy),
                args.as_of,
                lookback_trading_days=args.lookback_trading_days,
            )
        elif args.command == "advance-signals":
            result = ScanService(settings.data_dir, runs, signals).advance(registry.get(args.strategy), args.as_of)
        elif args.command == "backtest":
            service = BacktestService(settings.data_dir, runs, costs=CostConfig.from_settings(settings))
            strategy = _configured_strategy(args.strategy, args.config, registry)
            result = service.run(strategy, args.start, args.end)
        else:
            result = runs.get_scan(args.run_id) or runs.get_backtest(args.run_id)
            if result is None:
                print(json.dumps({"code": "run_not_found", "message": "run not found"}, ensure_ascii=False))
                return 4
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except DomainError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message, "details": exc.details}, ensure_ascii=False))
        return {
            "configuration_error": 2,
            "invalid_trade_date": 2,
            "data_unavailable": 3,
            "unknown_strategy": 4,
            "run_conflict": 5,
        }.get(exc.code, 6)
    except ValueError as exc:
        print(json.dumps({"code": "configuration_error", "message": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({"code": "internal_error", "message": str(exc)}, ensure_ascii=False))
        return 10


def _configured_strategy(strategy_id: str, path: str | None, registry):
    if not path:
        return registry.get(strategy_id)
    if strategy_id != "strong_gap_up_v1":
        raise ValueError(f"custom configuration is not supported for strategy {strategy_id}")
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return StrongGapUpStrategy(StrongGapConfig.model_validate(raw))


if __name__ == "__main__":
    sys.exit(main())
