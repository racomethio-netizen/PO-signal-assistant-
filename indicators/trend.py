"""Trend indicators: SMA and EMA.

All functions return a list the same length as the input, with ``None``
in positions where there isn't yet enough history to compute a value.
Nothing here ever extrapolates or fills gaps with invented numbers.
"""
from __future__ import annotations

from typing import List, Optional, Sequence


def sma(values: Sequence[float], period: int) -> List[Optional[float]]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: List[Optional[float]] = [None] * len(values)
    window_sum = 0.0
    for i, v in enumerate(values):
        window_sum += v
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            out[i] = window_sum / period
    return out


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    multiplier = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = (values[i] - prev) * multiplier + prev
        out[i] = prev
    return out


def latest(series: Sequence[Optional[float]]) -> Optional[float]:
    """Return the last non-None value in a series, or None if all empty."""
    for v in reversed(series):
        if v is not None:
            return v
    return None
