"""Long when the fast SMA is above the slow SMA at the close. Flat otherwise."""

from __future__ import annotations

import math

import pandas as pd

from app.strategies.base import OrderReq, Strategy


class SmaCrossover(Strategy):
    code_ref = "app.strategies.sma:SmaCrossover"
    description = "Go long the next open while the fast SMA is above the slow SMA. Flat otherwise."

    def signals(self, bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
        fast_n = int(params.get("fast", 20))
        slow_n = int(params.get("slow", 50))
        if fast_n < 2 or slow_n <= fast_n:
            raise ValueError("SMA windows must satisfy 2 <= fast < slow")
        columns = {}
        for symbol, frame in bars.items():
            close = frame["close"]
            fast = close.rolling(fast_n, min_periods=fast_n).mean()
            slow = close.rolling(slow_n, min_periods=slow_n).mean()
            long = ((fast > slow) & fast.notna() & slow.notna()).astype(float)
            columns[symbol] = long
        weights = pd.DataFrame(columns).sort_index().fillna(0.0)
        count = max(weights.shape[1], 1)
        return weights / count

    def orders_for_pipeline(self, *, bars, params, candidates, max_position_usd):
        if not bars:
            return []
        weights = self.signals(bars, params)
        if weights.empty:
            return []
        last = weights.iloc[-1]
        orders = []
        for symbol, weight in last.items():
            if float(weight) <= 0:
                continue
            price = float(bars[symbol]["close"].iloc[-1])
            if price <= 0:
                continue
            dollars = min(max_position_usd, max_position_usd * float(weight) * len(last))
            qty = math.floor(dollars / price)
            if qty >= 1:
                orders.append(OrderReq(symbol=symbol, side="buy", qty=qty))
        return orders
