"""Daily gap-up that held through the close. The live 5-minute playbook needs intraday bars."""

from __future__ import annotations

import math

import pandas as pd

from app.strategies.base import OrderReq, Strategy


class GapUpMomentum(Strategy):
    code_ref = "app.strategies.gap:GapUpMomentum"
    description = (
        "At the close, go long the next open if today's open gapped up past the prior close "
        "and the stock finished above the open. This is the daily version of gap-and-go. "
        "The 5-minute opening-range break needs intraday data this feed does not provide."
    )

    def signals(self, bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
        gap_min = float(params.get("gap_min", 0.10))
        hold_days = int(params.get("hold_days", 1))
        price_min = params.get("price_min")
        price_max = params.get("price_max")
        columns = {}
        for symbol, frame in bars.items():
            prev_close = frame["close"].shift(1)
            gap = frame["open"] / prev_close - 1.0
            held = frame["close"] > frame["open"]
            raw = (gap > gap_min) & held & prev_close.notna()
            if price_min is not None:
                raw = raw & (frame["close"] >= float(price_min))
            if price_max is not None:
                raw = raw & (frame["close"] <= float(price_max))
            desired = raw.astype(float).rolling(hold_days, min_periods=1).max()
            desired = desired.where(prev_close.notna(), 0.0).fillna(0.0)
            columns[symbol] = desired
        weights = pd.DataFrame(columns).sort_index().fillna(0.0)
        count = max(weights.shape[1], 1)
        return weights / count

    def orders_for_pipeline(self, *, bars, params, candidates, max_position_usd):
        orders = []
        stop_pct = float(params.get("stop_pct", 0.05))
        target_pct = float(params.get("target_pct", 0.10))
        for row in candidates:
            symbol = row["ticker"]
            price = row.get("price")
            if price is None and symbol in bars and not bars[symbol].empty:
                price = float(bars[symbol]["close"].iloc[-1])
            if not price or price <= 0:
                continue
            qty = math.floor(max_position_usd / float(price))
            if qty < 1:
                continue
            orders.append(
                OrderReq(
                    symbol=symbol,
                    side="buy",
                    qty=qty,
                    stop=round(float(price) * (1 - stop_pct), 4),
                    target=round(float(price) * (1 + target_pct), 4),
                )
            )
        return orders
