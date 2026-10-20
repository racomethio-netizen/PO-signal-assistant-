"""Trend indicators: SMA, EMA, ADX/+DI/-DI, and moving-average shape metrics.

All functions return a list the same length as the input, with ``None``
in positions where there isn't yet enough history to compute a value.
Nothing here ever extrapolates or fills gaps with invented numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from data.models import Candle


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


def slope(series: Sequence[Optional[float]], lookback: int = 5) -> Optional[float]:
    """Rate of change of a (moving-average) series over ``lookback`` bars,
    normalized by the earlier value so it's comparable across price scales.
    None if there isn't a valid pair of points ``lookback`` bars apart."""
    valid = [(i, v) for i, v in enumerate(series) if v is not None]
    if len(valid) < lookback + 1:
        return None
    current_i, current_v = valid[-1]
    target_i = current_i - lookback
    past = next((v for i, v in valid if i == target_i), None)
    if past is None or past == 0:
        return None
    return (current_v - past) / abs(past) / lookback


def separation(fast: Sequence[Optional[float]], slow: Sequence[Optional[float]]) -> Optional[float]:
    """Spread between two moving averages as a fraction of the slow one —
    a measure of trend conviction (wide separation = strong trend)."""
    f, s = latest(fast), latest(slow)
    if f is None or s is None or s == 0:
        return None
    return (f - s) / abs(s)


@dataclass
class ADXResult:
    plus_di: List[Optional[float]]
    minus_di: List[Optional[float]]
    adx: List[Optional[float]]


def _true_range_local(candles: Sequence[Candle]) -> List[float]:
    out: List[float] = [0.0] * len(candles)
    for i, c in enumerate(candles):
        if i == 0:
            out[i] = c.high - c.low
        else:
            prev_close = candles[i - 1].close
            out[i] = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
    return out


def adx(candles: Sequence[Candle], period: int = 14) -> ADXResult:
    """Wilder's ADX with +DI/-DI (directional movement index).

    Measures trend STRENGTH (not direction): high ADX means a strong trend
    is underway (either direction); low ADX means a weak/ranging market.
    +DI above -DI implies bullish directional pressure, and vice versa.
    """
    n = len(candles)
    plus_di: List[Optional[float]] = [None] * n
    minus_di: List[Optional[float]] = [None] * n
    adx_out: List[Optional[float]] = [None] * n
    if n < period * 2:
        return ADXResult(plus_di=plus_di, minus_di=minus_di, adx=adx_out)

    tr = _true_range_local(candles)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up_move = candles[i].high - candles[i - 1].high
        down_move = candles[i - 1].low - candles[i].low
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    # Wilder smoothing, seeded with a simple sum over the first period.
    smoothed_tr = sum(tr[1 : period + 1])
    smoothed_plus_dm = sum(plus_dm[1 : period + 1])
    smoothed_minus_dm = sum(minus_dm[1 : period + 1])

    dx_values: List[Optional[float]] = [None] * n

    def _compute_di(idx: int, s_tr: float, s_plus: float, s_minus: float) -> None:
        if s_tr == 0:
            plus_di[idx] = 0.0
            minus_di[idx] = 0.0
        else:
            plus_di[idx] = 100.0 * s_plus / s_tr
            minus_di[idx] = 100.0 * s_minus / s_tr
        denom = (plus_di[idx] or 0.0) + (minus_di[idx] or 0.0)
        dx_values[idx] = 100.0 * abs((plus_di[idx] or 0.0) - (minus_di[idx] or 0.0)) / denom if denom else 0.0

    _compute_di(period, smoothed_tr, smoothed_plus_dm, smoothed_minus_dm)

    for i in range(period + 1, n):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr[i]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm[i]
        _compute_di(i, smoothed_tr, smoothed_plus_dm, smoothed_minus_dm)

    # ADX itself is a Wilder-smoothed average of DX, starting after a
    # second `period` worth of DX values have accumulated.
    first_dx_idx = period
    if n >= first_dx_idx + period:
        window = [v for v in dx_values[first_dx_idx : first_dx_idx + period] if v is not None]
        if len(window) == period:
            adx_seed = sum(window) / period
            adx_idx = first_dx_idx + period - 1
            adx_out[adx_idx] = adx_seed
            prev_adx = adx_seed
            for i in range(adx_idx + 1, n):
                dx_i = dx_values[i] or 0.0
                prev_adx = (prev_adx * (period - 1) + dx_i) / period
                adx_out[i] = prev_adx

    return ADXResult(plus_di=plus_di, minus_di=minus_di, adx=adx_out)
