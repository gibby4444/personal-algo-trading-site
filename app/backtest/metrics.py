"""Summary stats for an equity curve and a trade list."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.backtest.engine import BacktestTrade


def periods_per_year(index: pd.DatetimeIndex) -> int:
    if len(index) < 3:
        return 252
    delta = pd.Series(index).diff().dropna().median()
    minutes = delta.total_seconds() / 60.0
    if minutes <= 2:
        return 252 * 390
    if minutes <= 90:
        return 252 * 7
    return 252


def compute_metrics(equity: pd.Series, trades: list[BacktestTrade]) -> dict:
    curve = equity.astype(float).dropna()
    rets = curve.pct_change().dropna()
    ppy = periods_per_year(curve.index)
    mean = float(rets.mean()) if len(rets) else 0.0
    std = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    sharpe = (mean / std) * math.sqrt(ppy) if std > 0 else 0.0
    downside = rets[rets < 0]
    down_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mean / down_std) * math.sqrt(ppy) if down_std > 0 else 0.0
    elapsed_days = max((curve.index[-1] - curve.index[0]).days, 1)
    years = elapsed_days / 365.25
    start_equity = float(curve.iloc[0])
    end_equity = float(curve.iloc[-1])
    cagr = (end_equity / start_equity) ** (1 / years) - 1 if start_equity > 0 else 0.0
    drawdown = curve / curve.cummax() - 1
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0
    wins = [trade for trade in trades if trade.pnl > 0]
    losses = [trade for trade in trades if trade.pnl < 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    gross_profit = sum(trade.pnl for trade in wins)
    gross_loss = -sum(trade.pnl for trade in losses)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    return {
        "sharpe": _clean(sharpe),
        "sortino": _clean(sortino),
        "cagr": _clean(cagr),
        "max_dd": _clean(max_dd),
        "win_rate": _clean(win_rate),
        "trades": len(trades),
        "profit_factor": None if profit_factor is None else _clean(profit_factor),
        "ending_equity": _clean(end_equity),
    }


def _clean(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return float(np.round(value, 6))
