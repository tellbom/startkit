from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STOCK_STRATEGY_",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    database_path: Path = Path("data/strategy.sqlite3")
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    data_freshness_hours: int = Field(default=36, ge=1)
    commission_bps: float = Field(default=3.0, ge=0)
    buy_slippage_bps: float = Field(default=5.0, ge=0)
    sell_slippage_bps: float = Field(default=5.0, ge=0)
    stamp_duty_bps: float = Field(default=5.0, ge=0)

    def ensure_runtime_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
