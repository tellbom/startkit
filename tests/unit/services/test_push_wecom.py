from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from scripts.push_wecom import MAX_MESSAGE_BYTES, _fit_utf8, format_message


def test_format_message_includes_actionable_signal():
    signal = SimpleNamespace(
        stock_name="示例股份",
        symbol="600000.SH",
        state=SimpleNamespace(value="entry_eligible"),
        rule_score=88.5,
        continuation_entry_date=None,
        earliest_entry_date=dt.date(2026, 8, 11),
        confirmation_date=dt.date(2026, 8, 10),
        risk_flags=["survivorship_bias"],
    )

    message = format_message(
        dt.date(2026, 8, 11),
        "强势向上跳空缺口",
        "2.1",
        {
            "triggered": 3,
            "advanced": 2,
            "daily_runs": [
                {"trade_date": "2026-08-10", "triggered": 3, "advanced": 2},
                {"trade_date": "2026-08-11", "triggered": 0, "advanced": 0},
            ],
        },
        [signal],
        1,
        sync={"universe_count": 300, "missing_symbols": ["688041"]},
    )

    assert "示例股份（600000.SH）" in message
    assert "评分：88.5" in message
    assert "今日新增 D0 候选：0（观察信号，不等于可执行）" in message
    assert "今日状态变化：0（包含确认、失效、过期等）" in message
    assert "当前可执行观察信号：1（仅此项进入下方清单）" in message
    assert "降级运行：缺失 1/300 只（0.33%）" in message
    assert "缺失股票：688041" in message


def test_fit_utf8_respects_wecom_payload_limit():
    result = _fit_utf8("策略结果" * 2000, MAX_MESSAGE_BYTES)

    assert len(result.encode("utf-8")) <= MAX_MESSAGE_BYTES
    assert result.endswith("（内容过长，已截断）")


def test_format_message_reports_complete_coverage():
    message = format_message(
        dt.date(2026, 8, 13),
        "策略",
        "2.1",
        {"daily_runs": []},
        [],
        0,
        sync={"universe_count": 300, "missing_symbols": []},
    )

    assert "数据覆盖：完整" in message
