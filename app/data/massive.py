"""Massive (formerly Polygon) stock aggregates. Activated by MASSIVE_API_KEY."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pandas as pd

from app.config import get_settings
from app.data.base import NotConfigured, OptionalNotSupported, ProviderError
from app.data.normalize import normalize_bars

_SPAN = {"1d": (1, "day"), "1day": (1, "day"), "1h": (1, "hour"), "1m": (1, "minute")}


class MassiveData:
    name = "massive"

    def bars(self, symbol: str, start, end, timeframe: str = "1d") -> pd.DataFrame:
        key = get_settings().massive_api_key
        if not key:
            raise NotConfigured("Set MASSIVE_API_KEY to use Massive")
        span = _SPAN.get(timeframe)
        if span is None:
            raise ProviderError(f"Unsupported timeframe {timeframe}")
        multiplier, timespan = span
        start_s = _as_date(start).isoformat()
        end_s = _as_date(end).isoformat()
        url = (
            f"https://api.massive.com/v2/aggs/ticker/{symbol}/range/"
            f"{multiplier}/{timespan}/{start_s}/{end_s}"
        )
        try:
            response = httpx.get(
                url,
                params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Massive request failed: {exc}") from exc
        rows = body.get("results") or []
        if not rows:
            raise ProviderError(f"Massive returned no bars for {symbol}")
        frame = pd.DataFrame(
            {
                "open": [row["o"] for row in rows],
                "high": [row["h"] for row in rows],
                "low": [row["l"] for row in rows],
                "close": [row["c"] for row in rows],
                "volume": [row["v"] for row in rows],
            },
            index=pd.to_datetime([row["t"] for row in rows], unit="ms", utc=True),
        )
        return normalize_bars(frame)

    def quote(self, symbol: str) -> dict:
        frame = self.bars(symbol, date.today() - timedelta(days=10), date.today(), "1d")
        return {"symbol": symbol, "price": float(frame["close"].iloc[-1]), "ts": frame.index[-1].isoformat()}

    def stream(self, symbols, on_bar) -> None:
        raise OptionalNotSupported("Massive streaming is not wired in this version")

    def option_chain(self, underlying: str, on) -> pd.DataFrame:
        raise OptionalNotSupported("Massive option chains are not wired in this version")


def _as_date(value) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value
