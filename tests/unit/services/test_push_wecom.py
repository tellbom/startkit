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
        {"triggered": 3, "advanced": 2},
        [signal],
        1,
    )

    assert "示例股份（600000.SH）" in message
    assert "评分：88.5" in message
    assert "当前可执行信号：1" in message


def test_fit_utf8_respects_wecom_payload_limit():
    result = _fit_utf8("策略结果" * 2000, MAX_MESSAGE_BYTES)

    assert len(result.encode("utf-8")) <= MAX_MESSAGE_BYTES
    assert result.endswith("（内容过长，已截断）")
