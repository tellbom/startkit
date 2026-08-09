from __future__ import annotations

import json

from stock_strategy_api.cli import _configured_strategy, build_parser, main
from stock_strategy_api.core.config import get_settings
from stock_strategy_api.strategies.registry import get_registry


def test_parser_exposes_required_commands():
    parser = build_parser()
    args = parser.parse_args(["scan", "--strategy", "strong_gap_up_v1", "--as-of", "2026-06-30"])
    assert args.command == "scan"
    assert args.lookback_trading_days is None


def test_custom_config_changes_hash(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"minimum_gap_pct": 0.02}), encoding="utf-8")
    strategy = _configured_strategy("strong_gap_up_v1", str(config), get_registry())
    assert strategy.config.minimum_gap_pct == 0.02
    assert strategy.config_hash() != get_registry().get("strong_gap_up_v1").config_hash()


def test_show_missing_run_has_nonzero_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    assert main(["show-run", "--run-id", "missing"]) == 4
    assert "run_not_found" in capsys.readouterr().out
    get_settings.cache_clear()


def test_unknown_strategy_has_distinct_cli_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    assert main(["scan", "--strategy", "missing", "--as-of", "2026-06-30"]) == 4
    assert "unknown_strategy" in capsys.readouterr().out
    get_settings.cache_clear()
