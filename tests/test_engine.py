"""Fill timing, costs, lookahead, and dirty data."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtest.checks import LookaheadError, assert_causal
from app.backtest.engine import run_backtest
from app.data.normalize import DataQualityError, normalize_bars
from app.strategies.gap import GapUpMomentum
from app.strategies.rotation import TqqqGoldRotation
from app.strategies.sma import SmaCrossover


def _bars(opens, closes):
    opens = np.asarray(opens, dtype=float)
    closes = np.asarray(closes, dtype=float)
    index = pd.bdate_range("2024-01-02", periods=len(opens), tz="UTC")
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
            "volume": np.full(len(opens), 1_000.0),
        },
        index=index,
    )
    return frame


def _hand_inputs():
    opens = [10, 10, 11, 12, 11, 10]
    closes = [10, 11, 12, 11, 10, 10]
    frame = _bars(opens, closes)
    weights = pd.DataFrame({"X": [0, 1, 1, 0, 0, 0]}, index=frame.index, dtype=float)
    return {"X": frame}, weights


def test_signal_at_close_fills_next_open_with_five_bps():
    bars, weights = _hand_inputs()
    result = run_backtest(bars, weights, initial_capital=10_000, cost_bps=5)
    # Lagged position earns the 11→12 move and gives it back on 12→11, minus entry and exit costs.
    assert result.equity.iloc[-1] == pytest.approx(9990.418958, abs=0.02)
    assert result.equity.max() < 11_000


def test_delaying_the_signal_one_more_bar_does_not_improve():
    bars, weights = _hand_inputs()
    causal = run_backtest(bars, weights, initial_capital=10_000, cost_bps=5)
    delayed = run_backtest(bars, weights.shift(1).fillna(0.0), initial_capital=10_000, cost_bps=5)
    assert delayed.equity.iloc[-1] < causal.equity.iloc[-1]


def test_same_bar_fill_would_have_been_better_and_is_not_used():
    """A fill on the signal bar would collect the 10→11 open move. The engine must not."""
    bars, weights = _hand_inputs()
    result = run_backtest(bars, weights, initial_capital=10_000, cost_bps=5)
    peeked = weights.shift(-1).fillna(0.0)
    # shift(-1) moves the next signal onto this bar. The engine then lags by one,
    # which puts the original signal into the position and collects the early move.
    cheat = run_backtest(bars, peeked, initial_capital=10_000, cost_bps=5)
    assert cheat.equity.iloc[-1] > result.equity.iloc[-1]
    assert cheat.equity.max() > 11_000


def test_nan_and_inf_are_rejected():
    frame = _bars([10, 10, 10, 10], [10, 10, 10, 10])
    dirty = frame.copy()
    dirty.iloc[2, dirty.columns.get_loc("close")] = np.nan
    weights = pd.DataFrame({"X": [0, 0, 0, 0]}, index=frame.index)
    with pytest.raises(DataQualityError):
        run_backtest({"X": dirty}, weights, initial_capital=1000, cost_bps=5)
    infinite = frame.copy()
    infinite.iloc[1, infinite.columns.get_loc("open")] = np.inf
    with pytest.raises(DataQualityError):
        run_backtest({"X": infinite}, weights, initial_capital=1000, cost_bps=5)


def test_missing_session_is_not_forward_filled():
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-05"], utc=True)
    frame = pd.DataFrame(
        {"open": [1, 1, 1], "high": [1, 1, 1], "low": [1, 1, 1], "close": [1, 1, 1], "volume": [1, 1, 1]},
        index=index,
    )
    cleaned = normalize_bars(frame)
    assert pd.Timestamp("2024-01-04", tz="UTC") not in cleaned.index
    assert len(cleaned) == 3


def test_future_close_signal_is_lookahead():
    frame = _bars([10, 10, 10, 10, 10, 10, 11, 12], [10, 10, 10, 10, 11, 12, 13, 9])

    class Cheat:
        def signals(self, bars, params):
            close = bars["X"]["close"]
            return (close.shift(-1) > close).astype(float).fillna(0.0).to_frame("X")

    with pytest.raises(LookaheadError):
        assert_causal(Cheat(), {"X": frame}, {})


def test_seeded_strategies_are_causal_on_synthetic_paths():
    index = pd.bdate_range("2022-01-03", periods=260, tz="UTC")
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, len(index))))
    open_ = np.concatenate([[close[0]], close[:-1]])
    frame = pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.001,
            "low": np.minimum(open_, close) * 0.999,
            "close": close,
            "volume": np.full(len(index), 1_000_000.0),
        },
        index=index,
    )
    bars = {"TSLA": frame, "QQQ": frame, "TQQQ": frame * 0.2, "GLD": frame * 1.5}
    # Scaling the whole frame keeps OHLC relationships.
    for name in ("TQQQ", "GLD"):
        bars[name] = frame.copy()
        bars[name][["open", "high", "low", "close"]] *= 0.4 if name == "TQQQ" else 1.8
    assert_causal(SmaCrossover(), {"TSLA": bars["TSLA"], "QQQ": bars["QQQ"]}, {"fast": 10, "slow": 30})
    assert_causal(GapUpMomentum(), {"TSLA": bars["TSLA"]}, {"gap_min": 0.02, "hold_days": 1})
    assert_causal(
        TqqqGoldRotation(),
        {"QQQ": bars["QQQ"], "TQQQ": bars["TQQQ"], "GLD": bars["GLD"]},
        {"ma_window": 50, "risk_symbol": "QQQ", "risk_on_symbol": "TQQQ", "risk_off_symbol": "GLD"},
    )
    result = run_backtest(
        {"TSLA": bars["TSLA"]},
        SmaCrossover().signals({"TSLA": bars["TSLA"]}, {"fast": 10, "slow": 30}),
        initial_capital=25_000,
        cost_bps=5,
    )
    assert np.isfinite(result.equity.to_numpy()).all()
    assert result.equity.iloc[-1] > 0
