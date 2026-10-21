"""Crypto bars through CCXT. The library is imported only when this adapter is selected."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from app.config import get_settings
from app.data.base import NotConfigured, OptionalNotSupported, ProviderError
from app.data.normalize import normalize_bars

_TF = {"1d": "1d", "1h": "1h", "1m": "1m"}


class CCXTData:
    name = "ccxt"

    def bars(self, symbol: str, start, end, timeframe: str = "1d") -> pd.DataFrame:
        settings = get_settings()
        tf = _TF.get(timeframe)
        if tf is None:
            raise ProviderError(f"Unsupported timeframe {timeframe}")
        try:
            import ccxt
        except ImportError as exc:
            raise NotConfigured("pip install ccxt to use the crypto adapter") from exc
        if not hasattr(ccxt, settings.ccxt_exchange):
            raise ProviderError(f"Unknown CCXT exchange {settings.ccxt_exchange}")
        exchange_cls = getattr(ccxt, settings.ccxt_exchange)
        exchange = exchange_cls(
            {
                "apiKey": settings.ccxt_api_key or "",
                "secret": settings.ccxt_api_secret or "",
                "enableRateLimit": True,
            }
        )
        since = int(datetime.combine(_as_date(start), datetime.min.time(), timezone.utc).timestamp() * 1000)
        try:
            rows = exchange.fetch_ohlcv(symbol, timeframe=tf, since=since, limit=1000)
        except Exception as exc:
            raise ProviderError(f"CCXT request failed: {exc}") from exc
        if not rows:
            raise ProviderError(f"CCXT returned no bars for {symbol}")
        frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        frame.index = pd.to_datetime(frame["ts"], unit="ms", utc=True)
        frame = frame.drop(columns=["ts"])
        end_ts = pd.Timestamp(_as_date(end), tz="UTC") + pd.Timedelta(days=1)
        frame = frame.loc[frame.index < end_ts]
        return normalize_bars(frame)

    def quote(self, symbol: str) -> dict:
        frame = self.bars(symbol, date.today() - timedelta_days(10), date.today(), "1d")
        return {"symbol": symbol, "price": float(frame["close"].iloc[-1]), "ts": frame.index[-1].isoformat()}

    def stream(self, symbols, on_bar) -> None:
        raise OptionalNotSupported("CCXT streaming is not wired in this version")

    def option_chain(self, underlying: str, on) -> pd.DataFrame:
        raise OptionalNotSupported("This CCXT adapter is spot OHLCV only")


def timedelta_days(days: int):
    from datetime import timedelta

    return timedelta(days=days)


def _as_date(value) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value
