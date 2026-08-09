from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_strategy_api.market_data.universe import UniverseService


def test_current_snapshot_historical_fallback_is_flagged(tmp_path):
    service = UniverseService(tmp_path)
    snapshot_date = dt.date(2026, 6, 30)
    service.save_fixture(
        pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "name": "浦发银行",
                    "in_date": snapshot_date,
                    "out_date": None,
                    "source": "current_snapshot",
                    "snapshot_date": snapshot_date,
                }
            ]
        )
    )
    result = service.members_as_of(dt.date(2025, 1, 1))
    assert result.symbols == ("600000",)
    assert result.survivorship_bias is True
    assert result.mode == "current_snapshot"
    assert result.data_date == snapshot_date
    assert result.stale is False


def test_old_current_snapshot_is_marked_stale(tmp_path):
    service = UniverseService(tmp_path)
    snapshot_date = dt.date(2026, 6, 30)
    service.save_fixture(
        pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "name": "浦发银行",
                    "in_date": snapshot_date,
                    "out_date": None,
                    "source": "current_snapshot",
                    "snapshot_date": snapshot_date,
                }
            ]
        )
    )
    result = service.members_as_of(dt.date(2026, 7, 1))
    assert result.stale is True
    assert "stale" in result.warning


def test_membership_boundaries_are_inclusive_and_bse_is_rejected(tmp_path):
    service = UniverseService(tmp_path)
    service.save_fixture(
        pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "in_date": "2026-01-01",
                    "out_date": "2026-01-20",
                    "source": "point_in_time",
                    "snapshot_date": "2026-01-01",
                },
                {
                    "symbol": "000001",
                    "name": "B",
                    "in_date": "2026-01-20",
                    "out_date": None,
                    "source": "point_in_time",
                    "snapshot_date": "2026-01-20",
                },
                {
                    "symbol": "830001",
                    "name": "C",
                    "in_date": "2026-01-01",
                    "out_date": None,
                    "source": "point_in_time",
                    "snapshot_date": "2026-01-01",
                },
            ]
        )
    )
    result = service.members_as_of(dt.date(2026, 1, 20))
    assert result.symbols == ("000001", "600000")
    assert result.mode == "point_in_time"


def test_failed_universe_refresh_preserves_previous_snapshot(tmp_path, monkeypatch):
    service = UniverseService(tmp_path)
    service.save_fixture(
        pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "in_date": "2026-01-01",
                    "out_date": None,
                    "source": "current_snapshot",
                    "snapshot_date": "2026-01-01",
                }
            ]
        )
    )
    original = service.path.read_bytes()

    def fail_fetch():
        raise ConnectionError("provider down")

    monkeypatch.setattr(UniverseService, "_fetch_current", staticmethod(fail_fetch))
    with pytest.raises(ConnectionError, match="provider down"):
        service.fetch_and_save(dt.date(2026, 1, 2))
    assert service.path.read_bytes() == original
