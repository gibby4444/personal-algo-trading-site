"""Strategy interface. Params are data. Strategies do not exec anything the user types."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class OrderReq:
    symbol: str
    side: str
    qty: float
    type: str = "market"
    limit_price: float | None = None
    stop: float | None = None
    target: float | None = None


class Strategy:
    code_ref: str = ""
    description: str = ""

    def symbols(self, requested: list[str], params: dict) -> list[str]:
        return requested

    def signals(self, bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
        raise NotImplementedError

    def orders_for_pipeline(
        self,
        *,
        bars: dict[str, pd.DataFrame],
        params: dict,
        candidates: list[dict],
        max_position_usd: float,
    ) -> list[OrderReq]:
        return []


def _params(params: dict) -> dict:
    return params or {}
