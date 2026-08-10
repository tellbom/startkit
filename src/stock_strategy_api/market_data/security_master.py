from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stock_strategy_api.core.clock import as_date
from stock_strategy_api.core.errors import DataUnavailableError
from stock_strategy_api.market_data.parquet_store import atomic_write_parquet
from stock_strategy_api.market_data.paths import security_snapshot_path
from stock_strategy_api.market_data.retry import call_with_retry
from stock_strategy_api.market_data.symbols import is_shanghai_or_shenzhen, normalize_symbol


@dataclass(frozen=True, slots=True)
class SecuritySnapshot:
    symbol: str
    name: str
    exchange: str
    listing_date: dt.date
    status: str
    effective_date: dt.date
    source: str


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligible: bool
    reasons: tuple[str, ...]
    security: SecuritySnapshot | None
    security_master_pit: bool


class SecurityMasterService:
    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root)

    def fetch_and_save(self, effective_date: dt.date | str | None = None) -> pd.DataFrame:
        date = as_date(effective_date or dt.date.today())
        frame = self._fetch_current(date)
        if frame.empty:
            raise DataUnavailableError("security master provider returned no rows")
        exchanges = set(frame["exchange"].dropna().astype(str).str.lower())
        if not {"sh", "sz"}.issubset(exchanges):
            raise DataUnavailableError(
                "security master snapshot is incomplete",
                details={"required_exchanges": ["sh", "sz"], "actual_exchanges": sorted(exchanges)},
            )
        atomic_write_parquet(frame, security_snapshot_path(self.data_root, date.isoformat()))
        return frame

    def save_fixture(self, frame: pd.DataFrame, effective_date: dt.date | str) -> None:
        date = as_date(effective_date)
        required = {"symbol", "name", "exchange", "listing_date", "status", "effective_date", "source"}
        if not required.issubset(frame.columns):
            raise ValueError(f"security fixture is missing columns: {sorted(required - set(frame.columns))}")
        clean = frame.copy()
        clean["symbol"] = clean["symbol"].map(normalize_symbol)
        clean["listing_date"] = pd.to_datetime(clean["listing_date"]).dt.date
        clean["effective_date"] = pd.to_datetime(clean["effective_date"]).dt.date
        atomic_write_parquet(clean, security_snapshot_path(self.data_root, date.isoformat()))

    def load_snapshot(self, value: dt.date | str, *, allow_latest: bool = False) -> tuple[pd.DataFrame, bool]:
        date = as_date(value)
        exact = security_snapshot_path(self.data_root, date.isoformat())
        selected = exact
        pit = True
        if not selected.exists() and allow_latest:
            candidates = sorted((self.data_root / "security_master" / "snapshots").glob("*.parquet"))
            eligible = [path for path in candidates if path.stem <= date.isoformat()]
            if eligible:
                selected = eligible[-1]
                pit = selected.stem == date.isoformat()
            elif candidates:
                selected = candidates[-1]
                pit = False
        if not selected.exists():
            raise DataUnavailableError(f"security master snapshot is missing for {date}")
        frame = pd.read_parquet(selected)
        frame["listing_date"] = pd.to_datetime(frame["listing_date"]).dt.date
        frame["effective_date"] = pd.to_datetime(frame["effective_date"]).dt.date
        return frame, pit

    def evaluate(
        self,
        symbol: str,
        as_of: dt.date | str,
        day_bar: pd.Series | dict | None,
        *,
        minimum_listing_days: int = 60,
        allow_latest_snapshot: bool = False,
    ) -> EligibilityResult:
        date = as_date(as_of)
        frame, pit = self.load_snapshot(date, allow_latest=allow_latest_snapshot)
        normalized = normalize_symbol(symbol)
        row = frame.loc[frame["symbol"] == normalized]
        if row.empty:
            return EligibilityResult(False, ("security_master_missing",), None, pit)
        item = row.iloc[-1]
        required_master = ("name", "exchange", "listing_date", "status", "effective_date", "source")
        if any(pd.isna(item.get(column)) or str(item.get(column)).strip() == "" for column in required_master):
            return EligibilityResult(False, ("security_master_incomplete",), None, pit)
        security = SecuritySnapshot(
            symbol=normalized,
            name=str(item["name"]),
            exchange=str(item["exchange"]).lower(),
            listing_date=item["listing_date"],
            status=str(item["status"]).lower(),
            effective_date=item["effective_date"],
            source=str(item["source"]),
        )
        reasons: list[str] = []
        if security.exchange not in {"sh", "sz"} or not is_shanghai_or_shenzhen(normalized):
            reasons.append("unsupported_exchange")
        normalized_name = security.name.upper().replace(" ", "")
        if security.status not in {"active", "normal", "上市"}:
            reasons.append("ineligible_status")
        if re.match(r"^(?:S\*?ST|\*?ST)", normalized_name):
            reasons.append("st_stock")
        if "退" in security.name or "退市" in security.status:
            reasons.append("delisting_stock")
        if (date - security.listing_date).days < minimum_listing_days:
            reasons.append("listing_age_below_minimum")
        if day_bar is None:
            reasons.append("missing_day_bar")
        else:
            bar = dict(day_bar)
            required = ("open", "high", "low", "close", "volume")
            if any(pd.isna(bar.get(column)) for column in required):
                reasons.append("invalid_day_bar")
            elif float(bar["volume"]) <= 0:
                reasons.append("suspended")
        return EligibilityResult(not reasons, tuple(dict.fromkeys(reasons)), security, pit)

    @staticmethod
    def _fetch_current(effective_date: dt.date) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataUnavailableError("akshare is not installed") from exc
        frames: list[pd.DataFrame] = []
        attempts = (
            ("stock_info_sh_name_code", {"symbol": "主板A股"}, "sh"),
            ("stock_info_sh_name_code", {"symbol": "科创板"}, "sh"),
            ("stock_info_sz_name_code", {"symbol": "A股列表"}, "sz"),
        )
        for function_name, kwargs, exchange in attempts:
            function = getattr(ak, function_name, None)
            if function is None:
                continue
            try:
                raw = call_with_retry(function, attempts=2, label=function_name, **kwargs)
                normalized = _normalize_master(raw, exchange, effective_date, function_name)
                if not normalized.empty:
                    frames.append(normalized)
            except Exception:
                continue
        if not frames:
            raise DataUnavailableError("all security master providers failed")
        return pd.concat(frames, ignore_index=True).drop_duplicates("symbol", keep="last")


def _normalize_master(
    frame: pd.DataFrame | None,
    exchange: str,
    effective_date: dt.date,
    source: str,
) -> pd.DataFrame:
    columns = ["symbol", "name", "exchange", "listing_date", "status", "effective_date", "source"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    code_column = next((c for c in ("证券代码", "A股代码", "公司代码", "code") if c in frame.columns), None)
    name_column = next((c for c in ("证券简称", "A股简称", "公司简称", "name") if c in frame.columns), None)
    date_column = next((c for c in ("上市日期", "A股上市日期", "listing_date") if c in frame.columns), None)
    if not code_column or not name_column or not date_column:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(
        {
            "symbol": frame[code_column].astype(str).str.extract(r"(\d{6})", expand=False),
            "name": frame[name_column].astype(str),
            "listing_date": pd.to_datetime(frame[date_column], errors="coerce").dt.date,
        }
    ).dropna(subset=["symbol", "listing_date"])
    result["symbol"] = result["symbol"].map(normalize_symbol)
    result["exchange"] = exchange
    result["status"] = "active"
    result["effective_date"] = effective_date
    result["source"] = source
    return result[columns]
