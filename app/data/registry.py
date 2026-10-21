"""Pick the data provider from DATA_PROVIDER. yfinance is the default."""

from __future__ import annotations

from app.config import get_settings
from app.data.alpaca import AlpacaData
from app.data.base import ProviderError
from app.data.ccxt_provider import CCXTData
from app.data.databento_provider import DatabentoData
from app.data.fixture import FixtureProvider
from app.data.massive import MassiveData
from app.data.thetadata import ThetaData
from app.data.yfinance_provider import YFinanceData

_BUILDERS = {
    "yfinance": YFinanceData,
    "yahoo": YFinanceData,
    "alpaca": AlpacaData,
    "massive": MassiveData,
    "polygon": MassiveData,
    "databento": DatabentoData,
    "ccxt": CCXTData,
    "thetadata": ThetaData,
    "fixture": FixtureProvider,
}


def get_provider():
    name = get_settings().data_provider
    builder = _BUILDERS.get(name)
    if builder is None:
        raise ProviderError(
            f"Unknown DATA_PROVIDER {name!r}. Use yfinance, alpaca, massive, databento, ccxt, or thetadata."
        )
    return builder()


def load_bars(provider, symbols: list[str], start, end, timeframe: str = "1d") -> dict:
    if hasattr(provider, "bars_many"):
        return provider.bars_many(symbols, start, end, timeframe)
    return {symbol: provider.bars(symbol, start, end, timeframe) for symbol in symbols}
