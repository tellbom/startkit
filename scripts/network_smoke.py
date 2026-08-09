from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


def _collect_symbol(arguments: tuple[str, str, str, str, int, bool]) -> list[dict]:
    data_dir, symbol, start, end, socket_timeout, single_attempt = arguments
    socket.setdefaulttimeout(socket_timeout)
    from stock_strategy_api.market_data.ohlcv import OHLCVCollector

    if single_attempt:
        import stock_strategy_api.market_data.ohlcv as ohlcv_module

        def one_attempt(function, *args, **kwargs):
            kwargs.pop("attempts", None)
            kwargs.pop("backoff_seconds", None)
            kwargs.pop("label", None)
            return function(*args, **kwargs)

        ohlcv_module.call_with_retry = one_attempt

    collector = OHLCVCollector(Path(data_dir), start_date=dt.date.fromisoformat(start))
    results: list[dict] = []
    for adjustment in ("raw", "qfq"):
        try:
            result = collector.collect_symbol(symbol, adjustment, dt.date.fromisoformat(end))
            results.append(
                {
                    "symbol": symbol,
                    "adjustment": adjustment,
                    "success": True,
                    "rows_new": result.rows_new,
                    "rows_total": result.rows_total,
                    "last_date": result.last_date.isoformat() if result.last_date else None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "symbol": symbol,
                    "adjustment": adjustment,
                    "success": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an explicit real-network CSI300 OHLCV smoke audit")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--socket-timeout", type=int, default=10)
    parser.add_argument("--single-attempt", action="store_true")
    parser.add_argument("--symbol", help="Fetch one symbol; intended for a subprocess supervisor")
    args = parser.parse_args()

    from stock_strategy_api.market_data.universe import UniverseService

    data_dir = Path(args.data_dir)
    as_of = dt.date.fromisoformat(args.as_of)
    if args.symbol:
        result = _collect_symbol(
            (
                str(data_dir),
                args.symbol,
                args.start,
                args.as_of,
                args.socket_timeout,
                args.single_attempt,
            )
        )
        print(json.dumps(result, ensure_ascii=False))
        return 1 if any(not item["success"] for item in result) else 0
    snapshot = UniverseService(data_dir).members_as_of(as_of)
    work = [
        (str(data_dir), symbol, args.start, args.as_of, args.socket_timeout, args.single_attempt)
        for symbol in snapshot.symbols
    ]
    results: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(_collect_symbol, item) for item in work]
        for index, future in enumerate(as_completed(futures), start=1):
            results.extend(future.result())
            if index % 25 == 0:
                print(f"progress={index}/{len(work)}", flush=True)
    failures = [result for result in results if not result["success"]]
    summary = {
        "as_of": args.as_of,
        "universe_count": len(snapshot.symbols),
        "adjustment_fetches": len(results),
        "succeeded": len(results) - len(failures),
        "failed": len(failures),
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
