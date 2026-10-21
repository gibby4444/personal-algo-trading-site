"""Put vendor frames onto one schema. Missing sessions are dropped, never forward-filled."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

OHLC = ["open", "high", "low", "close", "volume"]


class DataQualityError(ValueError):
    pass


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise DataQualityError("No bars returned")
    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame.columns = [str(col).strip().lower().replace(" ", "_") for col in frame.columns]
    rename = {"adj_close": "adj_close"}
    frame = frame.rename(columns=rename)
    missing = [col for col in ("open", "high", "low", "close") if col not in frame.columns]
    if missing:
        raise DataQualityError(f"Bars are missing {missing}")
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    frame = frame[OHLC]
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    # Drop rows that are entirely empty. Do not fill the hole they leave.
    empty = frame[["open", "high", "low", "close"]].isna().all(axis=1)
    frame = frame.loc[~empty]
    for col in OHLC:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if frame.empty:
        raise DataQualityError("No usable bars after dropping empty rows")
    return frame


def assert_finite(df: pd.DataFrame, label: str) -> None:
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        return
    values = numeric.to_numpy(dtype=float)
    if values.size == 0:
        return
    if not np.isfinite(values).all():
        raise DataQualityError(f"{label} contains NaN or inf")


def assert_ohlc(df: pd.DataFrame, label: str) -> None:
    assert_finite(df[OHLC], label)
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise DataQualityError(f"{label} has a non-positive price")
    if (df["volume"] < 0).any():
        raise DataQualityError(f"{label} has negative volume")
    if (df["high"] + 1e-9 < df["low"]).any():
        raise DataQualityError(f"{label} has high below low")
    if (df["high"] + 1e-9 < df[["open", "close"]].max(axis=1)).any():
        raise DataQualityError(f"{label} has a high below the open or close")
    if (df["low"] - 1e-9 > df[["open", "close"]].min(axis=1)).any():
        raise DataQualityError(f"{label} has a low above the open or close")


def finite_number(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
