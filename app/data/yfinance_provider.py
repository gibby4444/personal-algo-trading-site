"""Free daily bars from Yahoo via yfinance. Fine for research, not for live orders."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

from app.data.base import OptionalNotSupported, ProviderError
from app.data.normalize import normalize_bars

_CACHE: dict[tuple, tuple[float, pd.DataFrame]] = {}
_MISSING: dict[tuple, float] = {}
_TTL_SEC = 15 * 60

_INTERVALS = {"1d": "1d", "1day": "1d", "1h": "1h", "1m": "1m"}


class YFinanceData:
    name = "yfinance"

    def bars(self, symbol: str, start, end, timeframe: str = "1d") -> pd.DataFrame:
        frames = self.bars_many([symbol], start, end, timeframe)
        if symbol not in frames:
            raise ProviderError(f"yfinance returned no bars for {symbol}")
        return frames[symbol]

    def bars_many(self, symbols: list[str], start, end, timeframe: str = "1d") -> dict[str, pd.DataFrame]:
        interval = _INTERVALS.get(timeframe, timeframe)
        if interval not in _INTERVALS.values():
            raise ProviderError(f"Unsupported timeframe {timeframe}. Use 1d, 1h, or 1m.")
        start_d = _as_date(start)
        end_d = _as_date(end)
        # yfinance treats end as exclusive.
        end_exclusive = end_d + timedelta(days=1)
        missing = []
        found: dict[str, pd.DataFrame] = {}
        now = time.time()
        for symbol in symbols:
            key = (symbol, start_d.isoformat(), end_d.isoformat(), interval)
            cached = _CACHE.get(key)
            if cached and now - cached[0] < _TTL_SEC:
                found[symbol] = cached[1].copy()
            elif now - _MISSING.get(key, 0) < _TTL_SEC:
                continue
            else:
                missing.append(symbol)
        if missing:
            # A delisted name must not throw away the rest of the watchlist.
            downloaded = _download(missing, start_d, end_exclusive, interval)
            for symbol in missing:
                key = (symbol, start_d.isoformat(), end_d.isoformat(), interval)
                frame = downloaded.get(symbol)
                if frame is None:
                    _MISSING[key] = now
                    continue
                _CACHE[key] = (now, frame)
                found[symbol] = frame.copy()
        if not found:
            raise ProviderError(f"yfinance returned no usable bars for {symbols}")
        return found

    def quote(self, symbol: str) -> dict:
        end = date.today()
        start = end - timedelta(days=10)
        frame = self.bars(symbol, start, end, "1d")
        last = frame.iloc[-1]
        return {
            "symbol": symbol,
            "price": float(last["close"]),
            "ts": frame.index[-1].isoformat(),
        }

    def stream(self, symbols: list[str], on_bar) -> None:
        raise OptionalNotSupported("yfinance has no streaming bar feed in this desk")

    def option_chain(self, underlying: str, on) -> pd.DataFrame:
        raise OptionalNotSupported("Options history is a ThetaData or Alpaca adapter, not yfinance")


def _as_date(value) -> date:
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value.date() if hasattr(value, "date") else value


def _download(symbols: list[str], start: date, end_exclusive: date, interval: str) -> dict[str, pd.DataFrame]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ProviderError("yfinance is not installed") from exc
    try:
        raw = yf.download(
            tickers=" ".join(symbols),
            start=start.isoformat(),
            end=end_exclusive.isoformat(),
            interval=interval,
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:
        raise ProviderError(f"yfinance download failed: {exc}") from exc
    if raw is None or raw.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        piece = _slice_symbol(raw, symbol, len(symbols) == 1)
        if piece is None or piece.empty:
            continue
        try:
            out[symbol] = normalize_bars(piece)
        except Exception:
            continue
    return out


def _slice_symbol(raw: pd.DataFrame, symbol: str, single: bool) -> pd.DataFrame | None:
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        level1 = set(raw.columns.get_level_values(-1))
        if symbol in level0:
            return raw[symbol].copy()
        if symbol in level1:
            return raw.xs(symbol, axis=1, level=-1).copy()
        if single:
            flat = raw.copy()
            flat.columns = flat.columns.get_level_values(0)
            return flat
        return None
    return raw.copy() if single else None
