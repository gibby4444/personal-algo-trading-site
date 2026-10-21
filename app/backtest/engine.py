"""Portfolio backtest.

A weight on the close of bar t is filled at the open of bar t+1.
The position then earns the move from that open to the following open.
Nothing in the return of bar t is credited to the signal from bar t.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.data.normalize import DataQualityError, assert_finite, assert_ohlc, normalize_bars


@dataclass
class BacktestTrade:
    symbol: str
    entry_ts: datetime
    exit_ts: datetime
    qty: float
    pnl: float
    entry_price: float
    exit_price: float


@dataclass
class BacktestResult:
    equity: pd.Series
    drawdown: pd.Series
    trades: list[BacktestTrade]
    warnings: list[str]


def run_backtest(
    bars: dict[str, pd.DataFrame],
    weights: pd.DataFrame,
    *,
    initial_capital: float = 25_000.0,
    cost_bps: float = 5.0,
) -> BacktestResult:
    if initial_capital <= 0:
        raise DataQualityError("Capital must be positive")
    if cost_bps < 0:
        raise DataQualityError("Costs cannot be negative")
    if not bars:
        raise DataQualityError("No bars")
    if weights is None or weights.empty:
        raise DataQualityError("The strategy returned no signals")

    warnings: list[str] = []
    cleaned: dict[str, pd.DataFrame] = {}
    for symbol, frame in bars.items():
        normal = normalize_bars(frame)
        assert_ohlc(normal, symbol)
        cleaned[symbol] = normal

    w = weights.copy()
    w.index = pd.to_datetime(w.index, utc=True)
    w = w.sort_index()
    w = w[~w.index.duplicated(keep="last")]
    w.columns = [str(col) for col in w.columns]
    missing = [col for col in w.columns if col not in cleaned]
    if missing:
        raise DataQualityError(f"No bars for {missing}")
    assert_finite(w, "signals")

    opens = pd.DataFrame({symbol: cleaned[symbol]["open"] for symbol in w.columns}).sort_index()
    common = w.index
    for symbol in w.columns:
        common = common.intersection(opens[symbol].dropna().index)
    common = pd.DatetimeIndex(common).sort_values().unique()
    if len(common) < 3:
        raise DataQualityError("Fewer than 3 overlapping bars. Use a longer date range.")
    if len(common) < len(w.index):
        warnings.append(
            f"Aligned to {len(common)} overlapping sessions "
            f"({common.min().date()} → {common.max().date()}). "
            "Missing sessions were dropped, not forward-filled."
        )

    w = w.loc[common].astype(float)
    opens = opens.loc[common, list(w.columns)].astype(float)
    assert_finite(opens, "opens")
    assert_finite(w, "aligned signals")
    row_sum = w.sum(axis=1)
    if (row_sum > 1.0001).any():
        warnings.append("Some sessions allocate more than 100% of capital.")

    # The signal at this close is not the position during this bar.
    position = w.shift(1).fillna(0.0)
    next_open = opens.shift(-1)
    oo_ret = next_open / opens - 1.0
    valid = oo_ret.dropna().index
    if len(valid) < 2:
        raise DataQualityError("Not enough bars to fill at the next open")
    position = position.loc[valid]
    oo_ret = oo_ret.loc[valid]
    assert_finite(oo_ret, "returns")
    assert_finite(position, "positions")

    gross = (position * oo_ret).sum(axis=1)
    previous = position.shift(1).fillna(0.0)
    turnover = (position - previous).abs().sum(axis=1)
    cost_rate = cost_bps / 10_000.0
    net = gross - turnover * cost_rate
    assert_finite(net.to_frame("net"), "net returns")

    running = float(initial_capital)
    equity_values = [running]
    equity_index = [common[0]]
    start_equity: dict = {}
    for ts, ret in net.items():
        start_equity[ts] = running
        running *= 1.0 + float(ret)
        loc = common.get_loc(ts)
        equity_values.append(running)
        equity_index.append(common[int(loc) + 1])
    equity = pd.Series(equity_values, index=pd.to_datetime(equity_index, utc=True), dtype=float)
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    trades = _round_trips(position, opens, common, start_equity, cost_rate)
    return BacktestResult(equity=equity, drawdown=drawdown, trades=trades, warnings=warnings)


def _round_trips(position, opens, common, start_equity, cost_rate) -> list[BacktestTrade]:
    trades: list[BacktestTrade] = []
    final_ts = common[-1]
    for symbol in position.columns:
        active = None
        for ts, weight in position[symbol].items():
            weight = float(weight)
            if active is None and abs(weight) > 1e-12:
                active = (ts, weight, start_equity[ts])
            elif active is not None and abs(weight) <= 1e-12:
                trades.append(_close(symbol, active, ts, opens, cost_rate))
                active = None
        if active is not None:
            trades.append(_close(symbol, active, final_ts, opens, cost_rate))
    trades.sort(key=lambda trade: trade.entry_ts)
    return trades


def _close(symbol, active, exit_ts, opens, cost_rate) -> BacktestTrade:
    entry_ts, weight, equity_at_entry = active
    entry_price = float(opens.loc[entry_ts, symbol])
    exit_price = float(opens.loc[exit_ts, symbol])
    notional = abs(weight) * float(equity_at_entry)
    shares = notional / entry_price
    direction = 1.0 if weight > 0 else -1.0
    gross = direction * shares * (exit_price - entry_price)
    fees = notional * cost_rate + shares * exit_price * cost_rate
    return BacktestTrade(
        symbol=symbol,
        entry_ts=_py_dt(entry_ts),
        exit_ts=_py_dt(exit_ts),
        qty=direction * shares,
        pnl=gross - fees,
        entry_price=entry_price,
        exit_price=exit_price,
    )


def _py_dt(value) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.to_pydatetime()
