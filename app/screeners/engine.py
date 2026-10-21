"""Apply saved filters to the latest daily bar of a watchlist."""

from __future__ import annotations

import pandas as pd

from app.data.normalize import finite_number
from app.screeners.universes import meta_map, tickers


def run_filters(
    universe_name: str,
    filters: list[dict],
    bars_by_symbol: dict[str, pd.DataFrame],
) -> tuple[list[dict], list[str]]:
    meta = meta_map(universe_name)
    notes: list[str] = []
    rows: list[dict] = []
    for ticker in tickers(universe_name):
        frame = bars_by_symbol.get(ticker)
        if frame is None or len(frame) < 5:
            notes.append(f"{ticker}: not enough bars")
            continue
        metrics = _metrics(frame, meta.get(ticker, {}))
        if not _passes(metrics, filters):
            continue
        rows.append({"ticker": ticker, **metrics})
    rows.sort(key=lambda row: row.get("gap_pct") or -999, reverse=True)
    if any(item.get("field") == "float" for item in filters):
        notes.append("Float is not on the free daily feed, so a float filter matches nothing.")
    return rows, notes


def _metrics(frame: pd.DataFrame, meta: dict) -> dict:
    last = frame.iloc[-1]
    prev = frame.iloc[-2]
    prev_close = float(prev["close"])
    price = float(last["close"])
    gap_pct = (float(last["open"]) / prev_close - 1.0) * 100.0 if prev_close else float("nan")
    history = frame["volume"].iloc[:-1].tail(20)
    avg_vol = float(history.mean()) if len(history) else 0.0
    rel_vol = float(last["volume"]) / avg_vol if avg_vol else float("nan")
    return {
        "price": price,
        "gap_pct": gap_pct,
        "rel_vol": rel_vol,
        "volume": float(last["volume"]),
        "sector": meta.get("sector"),
        "name": meta.get("name"),
        "float": None,
        "news": None,
    }


def _passes(metrics: dict, filters: list[dict]) -> bool:
    for rule in filters or []:
        field = rule.get("field")
        op = str(rule.get("op", "")).strip()
        expected = rule.get("value")
        actual = metrics.get(field)
        if field == "float":
            return False
        if field == "sector":
            if str(actual or "").lower() != str(expected or "").lower():
                return False
            continue
        if not finite_number(actual):
            return False
        number = float(actual)
        if op == ">" and not number > float(expected):
            return False
        elif op == ">=" and not number >= float(expected):
            return False
        elif op == "<" and not number < float(expected):
            return False
        elif op == "<=" and not number <= float(expected):
            return False
        elif op in {"=", "=="} and not abs(number - float(expected)) < 1e-9:
            return False
        elif op == "between":
            low, high = float(expected[0]), float(expected[1])
            if not (low <= number <= high):
                return False
        elif op not in {">", ">=", "<", "<=", "=", "==", "between"}:
            return False
    return True
