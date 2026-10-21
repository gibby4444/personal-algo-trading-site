"""Market mood from SPY and QQQ versus their 200-day averages, and VIX versus 20."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.data.registry import get_provider, load_bars
from app.screeners.universes import BREADTH

VIX_SYMBOL = "^VIX"


def compute_mood(provider=None) -> dict:
    provider = provider or get_provider()
    end = date.today()
    start = end - timedelta(days=500)
    symbols = list(dict.fromkeys(["SPY", "QQQ", VIX_SYMBOL, *BREADTH]))
    bars = load_bars(provider, symbols, start, end, "1d")
    spy = _last_vs_ma(bars["SPY"], 200)
    qqq = _last_vs_ma(bars["QQQ"], 200)
    vix_close = float(bars[VIX_SYMBOL]["close"].iloc[-1])
    breadth, breadth_count = _breadth(bars)
    sentiment = _vix_sentiment(vix_close)
    regime, allow = _classify(spy["above"], qqq["above"], vix_close)
    as_of = bars["SPY"].index[-1]
    return {
        "regime": regime,
        "allow": allow,
        "vix": round(vix_close, 2),
        "vix_below_20": vix_close < 20,
        "spy_above_200dma": spy["above"],
        "qqq_above_200dma": qqq["above"],
        "spy_vs_200dma": spy["spread"],
        "qqq_vs_200dma": qqq["spread"],
        "spy_close": spy["close"],
        "qqq_close": qqq["close"],
        "sentiment": sentiment,
        "sentiment_note": "Scaled from VIX. This is not the CNN Fear & Greed index.",
        "breadth": breadth,
        "breadth_count": breadth_count,
        "breadth_note": "Share of a 10-ETF basket above its own 50-day average.",
        "ts": as_of.isoformat(),
        "provider": getattr(provider, "name", "unknown"),
    }


def _last_vs_ma(frame: pd.DataFrame, window: int) -> dict:
    close = frame["close"]
    average = close.rolling(window, min_periods=window).mean()
    if pd.isna(average.iloc[-1]):
        raise ValueError(f"Need at least {window} sessions")
    last = float(close.iloc[-1])
    ma = float(average.iloc[-1])
    return {"above": last > ma, "spread": (last / ma) - 1.0, "close": last, "ma": ma}


def _breadth(bars: dict[str, pd.DataFrame]) -> tuple[float | None, int]:
    hits = 0
    total = 0
    for symbol in BREADTH:
        frame = bars.get(symbol)
        if frame is None or len(frame) < 50:
            continue
        close = frame["close"]
        average = close.rolling(50, min_periods=50).mean()
        if pd.isna(average.iloc[-1]):
            continue
        total += 1
        if float(close.iloc[-1]) > float(average.iloc[-1]):
            hits += 1
    if total == 0:
        return None, 0
    return hits / total, total


def _vix_sentiment(vix: float) -> float:
    # 10 vol maps near 100, 35 vol maps near 0.
    raw = 100 - (vix - 10) * 4
    return round(min(100.0, max(0.0, raw)), 1)


def _classify(spy_above: bool, qqq_above: bool, vix: float) -> tuple[str, list[str]]:
    if spy_above and qqq_above and vix < 20:
        return "risk_on", ["long_momentum", "breakout"]
    if (not spy_above and not qqq_above) or vix >= 25:
        return "risk_off", ["defensive", "cash"]
    return "mixed", ["selective"]
