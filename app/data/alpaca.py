"""Alpaca market data. Uses the paper data keys. This module does not place orders."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pandas as pd

from app.config import get_settings
from app.data.base import NotConfigured, OptionalNotSupported, ProviderError
from app.data.normalize import normalize_bars

_TIMEFRAME = {"1d": "1Day", "1day": "1Day", "1h": "1Hour", "1m": "1Min"}


class AlpacaData:
    name = "alpaca"

    def bars(self, symbol: str, start, end, timeframe: str = "1d") -> pd.DataFrame:
        settings = get_settings()
        if not settings.paper_keys_present:
            raise NotConfigured("Set ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET to use Alpaca data")
        tf = _TIMEFRAME.get(timeframe)
        if tf is None:
            raise ProviderError(f"Unsupported timeframe {timeframe}")
        url = f"{settings.alpaca_data_base_url}/v2/stocks/{symbol}/bars"
        params = {
            "timeframe": tf,
            "start": _as_date(start).isoformat(),
            "end": _as_date(end).isoformat(),
            "adjustment": "split",
            "feed": "iex",
            "limit": 10000,
        }
        headers = {
            "APCA-API-KEY-ID": settings.alpaca_paper_key or "",
            "APCA-API-SECRET-KEY": settings.alpaca_paper_secret or "",
        }
        try:
            response = httpx.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Alpaca data request failed: {exc}") from exc
        rows = body.get("bars") or []
        if not rows:
            raise ProviderError(f"Alpaca returned no bars for {symbol}")
        frame = pd.DataFrame(
            {
                "open": [row["o"] for row in rows],
                "high": [row["h"] for row in rows],
                "low": [row["l"] for row in rows],
                "close": [row["c"] for row in rows],
                "volume": [row["v"] for row in rows],
            },
            index=pd.to_datetime([row["t"] for row in rows], utc=True),
        )
        return normalize_bars(frame)

    def quote(self, symbol: str) -> dict:
        frame = self.bars(symbol, date.today() - timedelta(days=10), date.today(), "1d")
        return {"symbol": symbol, "price": float(frame["close"].iloc[-1]), "ts": frame.index[-1].isoformat()}

    def stream(self, symbols, on_bar) -> None:
        raise OptionalNotSupported("Alpaca streaming is not wired in this version")

    def option_chain(self, underlying: str, on) -> pd.DataFrame:
        raise OptionalNotSupported("Alpaca option chains are not wired in this version")


def _as_date(value) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    if isinstance(value, date):
        return value
    return value
