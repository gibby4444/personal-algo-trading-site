"""Deterministic bars for tests and the local smoke test. No network."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.data.base import OptionalNotSupported, ProviderError
from app.data.normalize import normalize_bars

_CACHE: dict[str, pd.DataFrame] = {}


class FixtureProvider:
    name = "fixture"

    def bars(self, symbol: str, start, end, timeframe: str = "1d") -> pd.DataFrame:
        if timeframe not in {"1d", "1day"}:
            raise ProviderError("The fixture provider only has daily bars")
        frame = _series(symbol)
        start_ts = pd.Timestamp(_as_date(start), tz="UTC")
        end_ts = pd.Timestamp(_as_date(end), tz="UTC") + pd.Timedelta(days=1)
        sliced = frame.loc[(frame.index >= start_ts) & (frame.index < end_ts)]
        if sliced.empty:
            raise ProviderError(f"Fixture has no bars for {symbol} in that window")
        return sliced.copy()

    def bars_many(self, symbols, start, end, timeframe: str = "1d"):
        return {symbol: self.bars(symbol, start, end, timeframe) for symbol in symbols}

    def quote(self, symbol: str) -> dict:
        frame = self.bars(symbol, date.today() - timedelta(days=30), date.today(), "1d")
        return {
            "symbol": symbol,
            "price": float(frame["close"].iloc[-1]),
            "ts": frame.index[-1].isoformat(),
        }

    def stream(self, symbols, on_bar) -> None:
        raise OptionalNotSupported("fixture data does not stream")

    def option_chain(self, underlying: str, on) -> pd.DataFrame:
        raise OptionalNotSupported("fixture data has no option chain")


def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, pd.Timestamp) and not hasattr(value, "hour"):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value.date()


def _index(n: int) -> pd.DatetimeIndex:
    end = pd.Timestamp.now(tz="UTC").normalize()
    return pd.bdate_range(end=end, periods=n, tz="UTC")


def _ohlc(close: np.ndarray, open_: np.ndarray, volume: np.ndarray, index) -> pd.DataFrame:
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    frame = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=index,
    )
    return normalize_bars(frame)


def _trend(symbol: str, start: float, mu: float, sigma: float, seed: int, n: int = 420) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # A steady drift keeps index ETFs above a long average. Noise is small on purpose
    # so tests are not one unlucky path away from a different regime.
    close = start * np.exp(mu * np.arange(1, n + 1) + rng.normal(0, sigma, n).cumsum() * 0.15)
    open_ = np.empty(n)
    open_[0] = start
    open_[1:] = close[:-1]
    # Keep the last session's gap modest so only the planted name trips the screener.
    open_[-1] = float(close[-2]) * 1.002
    volume = rng.integers(1_000_000, 3_000_000, n).astype(float)
    volume[-1] = float(np.mean(volume[:-1]))
    return _ohlc(close, open_, volume, _index(n))


def _vix(n: int = 420) -> pd.DataFrame:
    index = _index(n)
    x = np.linspace(0, 8, n)
    close = 15.5 + np.sin(x) * 1.2
    close[-1] = 16.2
    open_ = close * 0.999
    open_[0] = close[0]
    volume = np.full(n, 0.0)
    return _ohlc(close, open_, volume, index)


def _sava(n: int = 420) -> pd.DataFrame:
    """A small-cap biotech name with a planted gap-up on the last session."""
    rng = np.random.default_rng(7)
    index = _index(n)
    close = 8.0 + np.sin(np.linspace(0, 12, n)) * 0.4 + rng.normal(0, 0.05, n)
    close = np.clip(close, 4, 14)
    open_ = close * 0.998
    volume = rng.integers(400_000, 700_000, n).astype(float)
    prev_close = float(close[-2])
    open_[-1] = prev_close * 1.18
    close[-1] = open_[-1] * 1.01
    volume[-1] = float(np.mean(volume[:-1])) * 4.2
    return _ohlc(close, open_, volume, index)


def _series(symbol: str) -> pd.DataFrame:
    if symbol in _CACHE:
        return _CACHE[symbol]
    seed = sum(ord(ch) for ch in symbol) + 20
    if symbol == "^VIX":
        frame = _vix()
    elif symbol == "SAVA":
        frame = _sava()
    elif symbol == "XLE":
        frame = _trend(symbol, 80, mu=-0.0008, sigma=0.01, seed=11)
    elif symbol in {"TQQQ"}:
        frame = _trend(symbol, 40, mu=0.001, sigma=0.01, seed=3)
    elif symbol in {"GLD"}:
        frame = _trend(symbol, 180, mu=0.0002, sigma=0.004, seed=4)
    elif symbol in {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XBI", "XLV", "IBB"}:
        frame = _trend(symbol, 100 + (seed % 50), mu=0.0008, sigma=0.004, seed=seed)
    else:
        frame = _trend(symbol, 50, mu=0.0004, sigma=0.008, seed=seed)
    _CACHE[symbol] = frame
    return frame
