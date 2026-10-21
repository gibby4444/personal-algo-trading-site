"""Hold TQQQ when QQQ is above its 200-day average, otherwise hold GLD."""

from __future__ import annotations

import math

import pandas as pd

from app.strategies.base import OrderReq, Strategy


class TqqqGoldRotation(Strategy):
    code_ref = "app.strategies.rotation:TqqqGoldRotation"
    description = (
        "At each close, if QQQ is above its 200-day average, hold TQQQ. "
        "Otherwise hold GLD. Cash until the average exists. Filled at the next open."
    )

    def symbols(self, requested: list[str], params: dict) -> list[str]:
        names = [
            params.get("risk_symbol", "QQQ"),
            params.get("risk_on_symbol", "TQQQ"),
            params.get("risk_off_symbol", "GLD"),
        ]
        return list(dict.fromkeys(names))

    def signals(self, bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
        window = int(params.get("ma_window", 200))
        risk = params.get("risk_symbol", "QQQ")
        risk_on = params.get("risk_on_symbol", "TQQQ")
        risk_off = params.get("risk_off_symbol", "GLD")
        if risk not in bars:
            raise ValueError(f"Rotation needs {risk} bars to compute the moving average")
        close = bars[risk]["close"]
        average = close.rolling(window, min_periods=window).mean()
        bullish = (close > average) & average.notna()
        weights = pd.DataFrame(index=close.index)
        weights[risk_on] = bullish.astype(float)
        weights[risk_off] = (~bullish & average.notna()).astype(float)
        return weights.fillna(0.0)

    def orders_for_pipeline(self, *, bars, params, candidates, max_position_usd):
        weights = self.signals(bars, params)
        if weights.empty:
            return []
        last = weights.iloc[-1]
        target = None
        for symbol, weight in last.items():
            if float(weight) >= 0.5:
                target = symbol
                break
        if target is None or target not in bars:
            return []
        price = float(bars[target]["close"].iloc[-1])
        if price <= 0:
            return []
        qty = math.floor(max_position_usd / price)
        if qty < 1:
            return []
        return [OrderReq(symbol=target, side="buy", qty=qty)]
