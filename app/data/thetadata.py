"""ThetaData history through the local Theta Terminal. Needs that sidecar running."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pandas as pd

from app.config import get_settings
from app.data.base import NotConfigured, OptionalNotSupported, ProviderError
from app.data.normalize import normalize_bars


class ThetaData:
    name = "thetadata"

    def bars(self, symbol: str, start, end, timeframe: str = "1d") -> pd.DataFrame:
        settings = get_settings()
        if not settings.thetadata_user:
            raise NotConfigured("Set THETADATA_USER (and run Theta Terminal) to use ThetaData")
        if timeframe not in {"1d", "1day"}:
            raise ProviderError("ThetaData in this version requests end-of-day stock bars only")
        url = f"{settings.thetadata_base_url}/v2/hist/stock/eod"
        params = {
            "root": symbol,
            "start_date": _as_date(start).strftime("%Y%m%d"),
            "end_date": _as_date(end).strftime("%Y%m%d"),
        }
        try:
            response = httpx.get(url, params=params, timeout=30)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(
                "ThetaData request failed. The Theta Terminal sidecar must be running. "
                f"Detail: {exc}"
            ) from exc
        payload = body.get("response") if isinstance(body, dict) else None
        if not payload:
            raise ProviderError(f"ThetaData returned no rows for {symbol}")
        frame = _frame_from_terminal(payload)
        return normalize_bars(frame)

    def quote(self, symbol: str) -> dict:
        frame = self.bars(symbol, date.today() - timedelta(days=15), date.today(), "1d")
        return {"symbol": symbol, "price": float(frame["close"].iloc[-1]), "ts": frame.index[-1].isoformat()}

    def stream(self, symbols, on_bar) -> None:
        raise OptionalNotSupported("ThetaData streaming is not wired in this version")

    def option_chain(self, underlying: str, on) -> pd.DataFrame:
        settings = get_settings()
        if not settings.thetadata_user:
            raise NotConfigured("Set THETADATA_USER to query ThetaData")
        raise OptionalNotSupported(
            "Option-chain pulls are left to a later pass. Point a backtest at stock EOD first."
        )


def _as_date(value) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value


def _frame_from_terminal(payload) -> pd.DataFrame:
    """Accept a list of dicts or a header/rows pair without guessing prices that are not there."""
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        frame = pd.DataFrame(payload)
        frame.columns = [str(col).lower() for col in frame.columns]
        if "date" in frame.columns:
            frame.index = pd.to_datetime(frame["date"].astype(str), utc=True)
        return frame
    raise ProviderError("Unrecognized Theta Terminal payload. Expected a list of bar objects.")
