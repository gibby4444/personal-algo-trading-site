"""Databento futures history. The SDK is imported only when this adapter is selected."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.config import get_settings
from app.data.base import NotConfigured, OptionalNotSupported, ProviderError
from app.data.normalize import normalize_bars


class DatabentoData:
    name = "databento"

    def bars(self, symbol: str, start, end, timeframe: str = "1d") -> pd.DataFrame:
        settings = get_settings()
        if not settings.databento_api_key:
            raise NotConfigured("Set DATABENTO_API_KEY to use Databento")
        if timeframe not in {"1d", "1day"}:
            raise ProviderError("The Databento adapter in this version requests ohlcv-1d only")
        try:
            import databento as db
        except ImportError as exc:
            raise NotConfigured("pip install databento to use the Databento adapter") from exc
        client = db.Historical(settings.databento_api_key)
        try:
            data = client.timeseries.get_range(
                dataset=settings.databento_dataset,
                schema="ohlcv-1d",
                symbols=[symbol],
                start=_as_date(start).isoformat(),
                end=_as_date(end).isoformat(),
            )
            frame = data.to_df()
        except Exception as exc:
            raise ProviderError(f"Databento request failed: {exc}") from exc
        rename = {col: col.lower() for col in frame.columns}
        frame = frame.rename(columns=rename)
        return normalize_bars(frame)

    def quote(self, symbol: str) -> dict:
        frame = self.bars(symbol, date.today() - timedelta(days=10), date.today(), "1d")
        return {"symbol": symbol, "price": float(frame["close"].iloc[-1]), "ts": frame.index[-1].isoformat()}

    def stream(self, symbols, on_bar) -> None:
        raise OptionalNotSupported("Databento live streaming is not wired in this version")

    def option_chain(self, underlying: str, on) -> pd.DataFrame:
        raise OptionalNotSupported("Use the futures schema on this adapter, not an option chain")


def _as_date(value) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value
