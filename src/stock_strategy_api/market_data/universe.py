from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stock_strategy_api.core.clock import as_date
from stock_strategy_api.core.errors import DataUnavailableError
from stock_strategy_api.market_data.parquet_store import atomic_write_parquet
from stock_strategy_api.market_data.paths import universe_path
from stock_strategy_api.market_data.retry import call_with_retry
from stock_strategy_api.market_data.symbols import is_shanghai_or_shenzhen, normalize_symbol


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    as_of: dt.date
    symbols: tuple[str, ...]
    mode: str
    survivorship_bias: bool
    warning: str | None = None
    data_date: dt.date | None = None
    stale: bool = False


class UniverseService:
    universe_key = "csi300"

    def __init__(self, data_root: Path | str) -> None:
        self.path = universe_path(data_root)

    def fetch_and_save(self, fetch_date: dt.date | str | None = None) -> pd.DataFrame:
        date = as_date(fetch_date or dt.date.today())
        incoming = self._fetch_current()
        if incoming.empty:
            raise DataUnavailableError("CSI 300 provider returned no constituents")
        existing = self._read()
        incoming_symbols = set(incoming["symbol"])
        if existing.empty:
            result = incoming.assign(
                in_date=date,
                out_date=None,
                source="current_snapshot",
                snapshot_date=date,
            )
        else:
            open_mask = existing["out_date"].isna()
            open_symbols = set(existing.loc[open_mask, "symbol"])
            removed = open_symbols - incoming_symbols
            added = incoming_symbols - open_symbols
            result = existing.copy()
            if removed:
                result.loc[open_mask & result["symbol"].isin(removed), "out_date"] = date - dt.timedelta(days=1)
            if added:
                names = incoming.set_index("symbol")["name"].to_dict()
                additions = pd.DataFrame(
                    [
                        {
                            "symbol": symbol,
                            "name": names.get(symbol, ""),
                            "in_date": date,
                            "out_date": None,
                            "source": "current_snapshot",
                            "snapshot_date": date,
                        }
                        for symbol in sorted(added)
                    ]
                )
                result = pd.concat([result, additions], ignore_index=True)
            result.loc[result["out_date"].isna(), "snapshot_date"] = date
        atomic_write_parquet(result, self.path)
        return result

    def save_fixture(self, frame: pd.DataFrame) -> None:
        required = {"symbol", "name", "in_date", "out_date", "source", "snapshot_date"}
        if not required.issubset(frame.columns):
            raise ValueError(f"universe fixture is missing columns: {sorted(required - set(frame.columns))}")
        clean = frame.copy()
        clean["symbol"] = clean["symbol"].map(normalize_symbol)
        for column in ("in_date", "out_date", "snapshot_date"):
            clean[column] = _date_objects(clean[column])
        atomic_write_parquet(clean, self.path)

    def members_as_of(self, value: dt.date | str) -> UniverseSnapshot:
        date = as_date(value)
        frame = self._read()
        if frame.empty:
            raise DataUnavailableError("CSI 300 membership data is missing")
        selected = frame.loc[(frame["in_date"] <= date) & (frame["out_date"].isna() | (frame["out_date"] >= date))]
        if selected.empty:
            current = frame.loc[frame["out_date"].isna() & (frame["source"] == "current_snapshot")]
            if current.empty:
                raise DataUnavailableError(f"CSI 300 has no membership coverage for {date}")
            selected = current
        point_in_time = bool((selected["source"] == "point_in_time").all())
        snapshot_dates = [value for value in selected["snapshot_date"] if value is not None]
        data_date = max(snapshot_dates) if snapshot_dates else None
        stale = bool(not point_in_time and data_date and data_date < date)
        warnings: list[str] = []
        if not point_in_time:
            warnings.append("Current constituent snapshot; historical results may have survivorship bias.")
        if stale:
            warnings.append(f"CSI 300 constituent snapshot is stale; latest snapshot is {data_date}.")
        symbols = tuple(sorted(symbol for symbol in selected["symbol"] if is_shanghai_or_shenzhen(symbol)))
        return UniverseSnapshot(
            as_of=date,
            symbols=symbols,
            mode="point_in_time" if point_in_time else "current_snapshot",
            survivorship_bias=not point_in_time,
            warning=" ".join(warnings) or None,
            data_date=data_date,
            stale=stale,
        )

    def names_as_of(self, value: dt.date | str) -> dict[str, str]:
        date = as_date(value)
        frame = self._read()
        selected = frame.loc[(frame["in_date"] <= date) & (frame["out_date"].isna() | (frame["out_date"] >= date))]
        return dict(zip(selected["symbol"], selected["name"], strict=True))

    def _read(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=["symbol", "name", "in_date", "out_date", "source", "snapshot_date"])
        frame = pd.read_parquet(self.path)
        for column in ("in_date", "out_date", "snapshot_date"):
            frame[column] = _date_objects(frame[column])
        return frame

    @staticmethod
    def _fetch_current() -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataUnavailableError("akshare is not installed") from exc
        errors: list[str] = []
        for name, kwargs in (
            ("index_stock_cons_csindex", {"symbol": "000300"}),
            ("index_stock_cons", {"symbol": "000300"}),
        ):
            function = getattr(ak, name, None)
            if function is None:
                continue
            try:
                raw = call_with_retry(function, attempts=2, label=f"CSI300 via {name}", **kwargs)
                result = _normalize_constituents(raw)
                if not result.empty:
                    return result
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise DataUnavailableError("all CSI 300 providers failed", details={"errors": errors})


def _normalize_constituents(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["symbol", "name"])
    code_candidates = ("成分券代码", "品种代码", "证券代码", "stock_code", "code")
    name_candidates = ("成分券名称", "品种名称", "证券简称", "stock_name", "name")
    code_column = next((column for column in code_candidates if column in frame.columns), None)
    name_column = next((column for column in name_candidates if column in frame.columns), None)
    if code_column is None:
        return pd.DataFrame(columns=["symbol", "name"])
    result = pd.DataFrame(
        {
            "symbol": frame[code_column].astype(str).str.extract(r"(\d{6})", expand=False),
            "name": frame[name_column].astype(str) if name_column else "",
        }
    ).dropna(subset=["symbol"])
    result["symbol"] = result["symbol"].map(normalize_symbol)
    result = result[result["symbol"].map(is_shanghai_or_shenzhen)]
    return result.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)


def _date_objects(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    return parsed.map(lambda value: value.date() if pd.notna(value) else None).astype(object)
