"""JSON bodies for the HTTP API."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class FilterIn(BaseModel):
    field: str
    op: str
    value: Any


class ScreenerIn(BaseModel):
    name: str
    universe: str
    filters: list[FilterIn] = Field(default_factory=list)
    regime_gate: str | None = None
    schedule_cron: str | None = None


class ParamsIn(BaseModel):
    params: dict


class ModeIn(BaseModel):
    mode: Literal["backtest", "paper", "live"]
    confirm_text: str | None = None
    totp: str | None = None


class BacktestIn(BaseModel):
    strategy_id: str
    symbols: list[str]
    start: date
    end: date
    timeframe: str = "1d"
    capital: float = 25000
    cost_bps: float = 5


class KillIn(BaseModel):
    scope: str = "all"
    flatten: bool = False
