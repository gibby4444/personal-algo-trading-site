"""The three seeded strategies. Add a class here, then point a row at its code_ref."""

from __future__ import annotations

from app.strategies.base import Strategy
from app.strategies.gap import GapUpMomentum
from app.strategies.rotation import TqqqGoldRotation
from app.strategies.sma import SmaCrossover

_INSTANCES: list[Strategy] = [SmaCrossover(), GapUpMomentum(), TqqqGoldRotation()]
STRATEGIES = {item.code_ref: item for item in _INSTANCES}


def get_strategy(code_ref: str) -> Strategy:
    try:
        return STRATEGIES[code_ref]
    except KeyError as exc:
        raise KeyError(f"Unknown strategy {code_ref}") from exc
