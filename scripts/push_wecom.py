from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_strategy_api.core.config import get_settings
from stock_strategy_api.core.errors import InvalidDateError
from stock_strategy_api.repositories.database import Database
from stock_strategy_api.repositories.run_repository import RunRepository
from stock_strategy_api.repositories.signal_repository import SignalRepository
from stock_strategy_api.services.data_sync import DataSyncService
from stock_strategy_api.services.recommendation_service import RecommendationService
from stock_strategy_api.services.scan_service import ScanService
from stock_strategy_api.strategies.registry import get_registry


STRATEGY_ID = "strong_gap_up_v1"
ACTIONABLE_STATES = ("entry_eligible", "continuation_entry")
DAILY_ACTION_KEY = "publish_daily_strategy_result"
MAX_MESSAGE_BYTES = 4000


def run(as_of: dt.date, webhook_url: str) -> dict:
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize()
    runs = RunRepository(database)
    signals = SignalRepository(database)
    registry = get_registry()
    strategy = registry.get(STRATEGY_ID)

    existing_action = runs.strategy_action(strategy, as_of, DAILY_ACTION_KEY)
    if existing_action:
        result = {
            "status": "duplicate_suppressed",
            "as_of": as_of.isoformat(),
            "action_status": existing_action["status"],
        }
        print(json.dumps(result, ensure_ascii=False))
        return result

    try:
        sync = DataSyncService(settings.data_dir).sync(as_of).to_dict()
    except InvalidDateError:
        result = {"status": "skipped", "as_of": as_of.isoformat(), "reason": "not_a_trading_day"}
        print(json.dumps(result, ensure_ascii=False))
        return result

    scan = ScanService(settings.data_dir, runs, signals).scan_recent(strategy, as_of)
    recommendations, total, _latest = RecommendationService(signals, runs, registry).list(
        strategy_id=STRATEGY_ID,
        state=ACTIONABLE_STATES,
        as_of=as_of,
        limit=200,
    )
    content = format_message(as_of, strategy.metadata().name, strategy.metadata().version, scan, recommendations, total)
    payload_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not runs.claim_strategy_action(strategy, as_of, DAILY_ACTION_KEY, payload_hash):
        result = {"status": "duplicate_suppressed", "as_of": as_of.isoformat(), "action_status": "claimed"}
        print(json.dumps(result, ensure_ascii=False))
        return result
    response = send_wecom(webhook_url, content)
    runs.finish_strategy_action(strategy, as_of, DAILY_ACTION_KEY, payload_hash, response)
    result = {
        "status": "sent",
        "as_of": as_of.isoformat(),
        "sync": sync,
        "scan": scan,
        "recommendation_count": total,
        "wecom": response,
    }
    print(json.dumps(result, ensure_ascii=False, default=str))
    return result


def format_message(as_of, strategy_name, strategy_version, scan, recommendations, total) -> str:
    lines = [
        f"**{strategy_name} v{strategy_version}**",
        f"> 交易日：{as_of.isoformat()}",
        f"> 本次扫描：触发 {scan.get('triggered', 0)}，推进 {scan.get('advanced', 0)}",
        f"> 当前可执行信号：{total}",
        "",
    ]
    if recommendations:
        for index, signal in enumerate(recommendations, 1):
            entry_date = signal.continuation_entry_date or signal.earliest_entry_date or signal.confirmation_date
            lines.append(
                f"{index}. **{signal.stock_name}（{signal.symbol}）**  "
                f"状态：{signal.state.value}｜评分：{signal.rule_score:.1f}｜入场日：{entry_date or '-'}"
            )
            if signal.risk_flags:
                lines.append(f"   风险：{', '.join(signal.risk_flags)}")
    else:
        lines.append("今日暂无可执行信号。")
    lines.extend(["", "规则筛选结果仅供研究观察，不构成投资建议或收益承诺。"])
    return _fit_utf8("\n".join(lines), MAX_MESSAGE_BYTES)


def send_wecom(webhook_url: str, content: str) -> dict:
    payload = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("errcode") != 0:
        raise RuntimeError(f"WeCom webhook rejected the message: {result}")
    return result


def _fit_utf8(value: str, limit: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    suffix = "\n\n（内容过长，已截断）"
    available = limit - len(suffix.encode("utf-8"))
    return raw[:available].decode("utf-8", errors="ignore") + suffix


def main() -> int:
    webhook_url = os.environ.get("WECOM_WEBHOOK_URL")
    if not webhook_url:
        print("WECOM_WEBHOOK_URL is required", file=sys.stderr)
        return 2
    timezone = ZoneInfo(os.environ.get("STOCK_STRATEGY_TIMEZONE", "Asia/Shanghai"))
    as_of = dt.datetime.now(timezone).date()
    if len(sys.argv) == 3 and sys.argv[1] == "--as-of":
        as_of = dt.date.fromisoformat(sys.argv[2])
    try:
        run(as_of, webhook_url)
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "as_of": as_of.isoformat(), "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
