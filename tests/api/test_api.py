from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from fastapi.testclient import TestClient

from stock_strategy_api.api import dependencies
from stock_strategy_api.api.dependencies import data_is_stale
from stock_strategy_api.core.config import Settings
from stock_strategy_api.main import create_app
from stock_strategy_api.market_data.calendar import CalendarService
from stock_strategy_api.strategies.base import GapPhase, SignalState
from stock_strategy_api.strategies.strong_gap_up_v1 import StrongGapUpStrategy
from tests.conftest import build_calendar_frame


def _entry_eligible_signal(qualifying_frames, d0, eligible):
    raw, qfq = qualifying_frames
    result = StrongGapUpStrategy().detect(
        raw, qfq, d0, eligible, universe_mode="point_in_time", survivorship_bias=False, calendar_source="fixture"
    )
    assert result.signal
    signal = result.signal
    signal.state = SignalState.ENTRY_ELIGIBLE
    signal.confirmation_date = d0
    return signal


def test_strategy_and_recommendation_contract(tmp_path, qualifying_frames, d0, eligible):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        strategy_response = client.get("/api/v1/strategies")
        assert strategy_response.status_code == 200
        assert strategy_response.json()["data"][0]["strategy_id"] == "strong_gap_up_v1"
        assert "properties" in strategy_response.json()["data"][0]["config_schema"]
        client.app.state.signals.upsert(_entry_eligible_signal(qualifying_frames, d0, eligible))
        response = client.get("/api/v1/recommendations")
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["total"] == 1
        assert body["data"][0]["recommendation_kind"] == "rule_based_observation"
        assert response.headers["X-Request-ID"]


def test_unknown_strategy_has_stable_error(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/strategies/missing")
        assert response.status_code == 404
        assert response.json()["code"] == "unknown_strategy"


def test_invalid_stock_has_stable_error(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/stocks/not-a-code/recommendations")
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"
        unsupported = client.get("/api/v1/stocks/830001/recommendations")
        assert unsupported.status_code == 422
        assert unsupported.json()["code"] == "invalid_request"


def test_openapi_exposes_only_read_backtest_operations(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        schema = client.get("/openapi.json").json()
    assert "/api/v1/backtests" in schema["paths"]
    assert set(schema["paths"]["/api/v1/backtests"]) == {"get"}
    assert "/api/v1/backtests/{run_id}/events" in schema["paths"]


def test_empty_recommendations_are_distinct_from_service_error(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/recommendations")
        assert response.status_code == 200
        assert response.json()["data"] == []
        assert response.json()["meta"]["stale"] is True
        missing = client.get("/api/v1/backtests/missing")
        assert missing.status_code == 404
        assert missing.json()["code"] == "resource_not_found"


def test_exhaustion_is_hidden_by_default_and_available_explicitly(tmp_path, qualifying_frames, d0, eligible):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        signal = _entry_eligible_signal(qualifying_frames, d0, eligible)
        signal.phase = GapPhase.EXHAUSTION
        signal.risk_flags.append("exhaustion_risk")
        client.app.state.signals.upsert(signal)
        assert client.get("/api/v1/recommendations").json()["meta"]["total"] == 0
        visible = client.get("/api/v1/recommendations?risk=include_exhaustion")
        assert visible.json()["meta"]["total"] == 1


def test_non_default_lifecycle_state_requires_explicit_filter(tmp_path, qualifying_frames, d0, eligible):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        signal = _entry_eligible_signal(qualifying_frames, d0, eligible)
        signal.state = SignalState.WATCHING_D1
        client.app.state.signals.upsert(signal)
        assert client.get("/api/v1/recommendations").json()["meta"]["total"] == 0
        watching = client.get("/api/v1/recommendations?state=watching_d1")
        assert watching.json()["meta"]["total"] == 1


def test_default_actionable_query_includes_continuation_entry(tmp_path, qualifying_frames, d0, eligible):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        signal = _entry_eligible_signal(qualifying_frames, d0, eligible)
        signal.state = SignalState.CONTINUATION_ENTRY
        signal.structure_validity = True
        signal.entry_validity = True
        signal.continuation_entry_date = d0 + dt.timedelta(days=1)
        client.app.state.signals.upsert(signal)
        legacy = signal.model_copy(update={"strategy_version": "2.0.0", "config_hash": "legacy"})
        client.app.state.signals.upsert(legacy)

        default = client.get("/api/v1/recommendations")
        assert default.status_code == 200
        assert default.json()["meta"]["total"] == 1
        assert default.json()["data"][0]["state"] == "continuation_entry"
        assert client.get("/api/v1/recommendations?state=entry_eligible").json()["meta"]["total"] == 0
        assert client.get("/api/v1/recommendations?version_scope=all").json()["meta"]["total"] == 2


def test_non_trading_as_of_and_pagination_errors_are_stable(tmp_path, d0):
    data_root = tmp_path / "data"
    CalendarService(data_root).save_fixture(build_calendar_frame(d0, periods=5))
    settings = Settings(data_dir=data_root, database_path=data_root / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        weekend = client.get("/api/v1/recommendations?as_of=2026-07-04")
        assert weekend.status_code == 422
        assert weekend.json()["code"] == "invalid_trade_date"
        invalid_page = client.get("/api/v1/recommendations?limit=0")
        assert invalid_page.status_code == 422
        assert invalid_page.json()["code"] == "validation_error"


def test_unhandled_error_is_sanitized(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    app = create_app(settings)

    @app.get("/_test/error")
    def injected_error():
        raise RuntimeError("private /tmp/example path")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_test/error")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "/tmp/example" not in response.text


def test_readiness_is_distinct_from_process_health(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "data" / "strategy.sqlite3")
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        readiness = client.get("/readyz")
        assert readiness.status_code == 503
        assert readiness.json()["status"] == "not_ready"
        assert readiness.json()["checks"]["successful_scan"] is False


def test_default_query_marks_an_old_trade_date_stale_but_historical_query_does_not(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    CalendarService(data_root).save_fixture(build_calendar_frame(dt.date(2026, 8, 3), periods=10))
    settings = Settings(data_dir=data_root, database_path=data_root / "strategy.sqlite3")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    now = dt.datetime(2026, 8, 10, 20, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    monkeypatch.setattr(dependencies, "now_shanghai", lambda: now)

    assert data_is_stale(request, now.isoformat(), as_of="2026-08-07", require_current=True)
    assert not data_is_stale(request, now.isoformat(), as_of="2026-08-07", require_current=False)
