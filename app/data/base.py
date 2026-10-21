"""Shared provider errors and the bar schema."""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class ProviderError(RuntimeError):
    pass


class NotConfigured(ProviderError):
    pass


class OptionalNotSupported(ProviderError):
    pass


class DataProvider(Protocol):
    name: str

    def bars(self, symbol: str, start, end, timeframe: str = "1d") -> pd.DataFrame:
        """Columns ts-index, open, high, low, close, volume. UTC. No cross-session forward fill."""

    def quote(self, symbol: str) -> dict:
        ...

    def stream(self, symbols: list[str], on_bar) -> None:
        ...

    def option_chain(self, underlying: str, on) -> pd.DataFrame:
        ...
