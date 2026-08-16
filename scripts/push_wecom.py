from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import time
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
DEFAULT_WECOM_WS_URL = "wss://openws.work.weixin.qq.com"
WECOM_SUBSCRIBE_CMD = "aibot_subscribe"
WECOM_SEND_CMD = "aibot_send_msg"


def run(as_of: dt.date, bot_id: str, bot_secret: str, chat_id: str, ws_url: str = DEFAULT_WECOM_WS_URL) -> dict:
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
    content = format_message(
        as_of,
        strategy.metadata().name,
        strategy.metadata().version,
        scan,
        recommendations,
        total,
        sync=sync,
    )
    payload_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not runs.claim_strategy_action(strategy, as_of, DAILY_ACTION_KEY, payload_hash):
        result = {"status": "duplicate_suppressed", "as_of": as_of.isoformat(), "action_status": "claimed"}
        print(json.dumps(result, ensure_ascii=False))
        return result
    response = send_wecom(bot_id, bot_secret, chat_id, content, ws_url=ws_url)
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


def format_message(as_of, strategy_name, strategy_version, scan, recommendations, total, *, sync=None) -> str:
    today = next(
        (item for item in scan.get("daily_runs", ()) if item.get("trade_date") == as_of.isoformat()),
        {},
    )
    today_triggered = int(today.get("triggered", scan.get("triggered", 0)))
    today_advanced = int(today.get("advanced", scan.get("advanced", 0)))
    lines = [
        f"**{strategy_name} v{strategy_version}**",
        f"> 交易日：{as_of.isoformat()}",
        f"> 今日新增 D0 候选：{today_triggered}（观察信号，不等于可执行）",
        f"> 今日状态变化：{today_advanced}（包含确认、失效、过期等）",
        f"> 当前可执行观察信号：{total}（仅此项进入下方清单）",
    ]
    missing_symbols = sorted(set((sync or {}).get("missing_symbols", ())) | set(scan.get("missing_symbols", ())))
    universe_count = int((sync or {}).get("universe_count", scan.get("universe_count", 300)))
    if missing_symbols:
        missing_ratio = len(missing_symbols) / universe_count if universe_count else 0
        lines.extend(
            [
                f"> ⚠️ 降级运行：缺失 {len(missing_symbols)}/{universe_count} 只（{missing_ratio:.2%}）",
                f"> 缺失股票：{', '.join(missing_symbols)}",
            ]
        )
    else:
        lines.append("> 数据覆盖：完整")
    lines.append("")
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


def send_wecom(
    bot_id: str,
    bot_secret: str,
    chat_id: str,
    content: str,
    *,
    ws_url: str = DEFAULT_WECOM_WS_URL,
    connect=None,
    timeout: float = 20,
) -> dict:
    """Authenticate over WeCom's long connection and actively push one Markdown message."""
    if connect is None:
        import certifi
        from websocket import create_connection

        connect = create_connection
        ssl_options = {"ca_certs": certifi.where()}
    else:
        ssl_options = None

    connect_options = {"timeout": timeout, "suppress_origin": True}
    if ssl_options:
        connect_options["sslopt"] = ssl_options
    connection = connect(ws_url, **connect_options)
    try:
        subscribe_req_id = _request_id(WECOM_SUBSCRIBE_CMD)
        _send_frame(
            connection,
            {
                "cmd": WECOM_SUBSCRIBE_CMD,
                "headers": {"req_id": subscribe_req_id},
                "body": {"bot_id": bot_id, "secret": bot_secret},
            },
        )
        subscribe_ack = _receive_ack(connection, subscribe_req_id, timeout)
        _require_success(subscribe_ack, "authentication")

        send_req_id = _request_id(WECOM_SEND_CMD)
        _send_frame(
            connection,
            {
                "cmd": WECOM_SEND_CMD,
                "headers": {"req_id": send_req_id},
                "body": {
                    "chatid": chat_id,
                    "msgtype": "markdown",
                    "markdown": {"content": content},
                },
            },
        )
        send_ack = _receive_ack(connection, send_req_id, timeout)
        _require_success(send_ack, "message push")
        return {
            "errcode": send_ack.get("errcode"),
            "errmsg": send_ack.get("errmsg"),
            "req_id": send_req_id,
            "transport": "wecom_aibot_websocket",
        }
    finally:
        connection.close()


def _request_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def _send_frame(connection, frame: dict) -> None:
    connection.send(json.dumps(frame, ensure_ascii=False))


def _receive_ack(connection, req_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for WeCom acknowledgement: {req_id}")
        connection.settimeout(remaining)
        raw = connection.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        frame = json.loads(raw)
        if frame.get("headers", {}).get("req_id") == req_id:
            return frame


def _require_success(frame: dict, operation: str) -> None:
    if frame.get("errcode") != 0:
        raise RuntimeError(
            f"WeCom {operation} failed: errcode={frame.get('errcode')}, errmsg={frame.get('errmsg')}"
        )


def _fit_utf8(value: str, limit: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    suffix = "\n\n（内容过长，已截断）"
    available = limit - len(suffix.encode("utf-8"))
    return raw[:available].decode("utf-8", errors="ignore") + suffix


def main() -> int:
    required = ("WECOM_BOT_ID", "WECOM_BOT_SECRET", "WECOM_BOT_CHAT_ID")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"{', '.join(missing)} is required", file=sys.stderr)
        return 2
    bot_id = os.environ["WECOM_BOT_ID"]
    bot_secret = os.environ["WECOM_BOT_SECRET"]
    chat_id = os.environ["WECOM_BOT_CHAT_ID"]
    ws_url = os.environ.get("WECOM_BOT_WS_URL", DEFAULT_WECOM_WS_URL)
    timezone = ZoneInfo(os.environ.get("STOCK_STRATEGY_TIMEZONE", "Asia/Shanghai"))
    as_of = dt.datetime.now(timezone).date()
    if len(sys.argv) == 3 and sys.argv[1] == "--as-of":
        as_of = dt.date.fromisoformat(sys.argv[2])
    try:
        run(as_of, bot_id, bot_secret, chat_id, ws_url)
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "as_of": as_of.isoformat(), "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
