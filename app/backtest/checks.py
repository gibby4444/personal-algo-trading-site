"""Lookahead and data-quality gates. A backtest that fails these is marked failed."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.data.normalize import DataQualityError, assert_finite

SURVIVORSHIP_NOTE = (
    "Symbols are the ones you typed, priced by the vendor as of this run. "
    "A screen built from today's listings leaves out names that delisted, so it has "
    "survivorship bias. Daily bars are stamped at 00:00 UTC on the session date. "
    "The signal is taken at the close and filled at the next bar's open."
)


class LookaheadError(DataQualityError):
    pass


def assert_causal(strategy, bars: dict[str, pd.DataFrame], params: dict, sample: int = 6) -> None:
    """A signal at t must be identical when every bar after t is removed."""
    full = strategy.signals(bars, params).sort_index()
    if full.empty or len(full.index) < 3:
        return
    assert_finite(full, "signals")
    index = full.index
    step = max(1, len(index) // max(sample, 1))
    picks = list(index[1::step])[:sample]
    if index[-1] not in picks:
        picks.append(index[-1])
    for ts in picks:
        sliced = {symbol: frame.loc[:ts] for symbol, frame in bars.items()}
        partial = strategy.signals(sliced, params)
        if ts not in partial.index:
            raise LookaheadError(f"Signal at {ts} disappeared when later bars were removed")
        left = full.loc[ts].astype(float)
        right = partial.loc[ts].astype(float).reindex(left.index)
        if right.isna().any() or not np.allclose(left.to_numpy(), right.to_numpy(), equal_nan=False):
            raise LookaheadError(
                f"Signal at {ts} changes when later bars are removed. That is lookahead."
            )


def assert_frame_clean(frame: pd.DataFrame, label: str) -> None:
    assert_finite(frame, label)
