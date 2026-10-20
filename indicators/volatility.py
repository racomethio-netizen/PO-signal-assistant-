"""Volatility indicators: ATR (Wilder), Bollinger Bands, Keltner Channels."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from data.models import Candle

from .trend import ema


def true_range(candles: Sequence[Candle]) -> List[float]:
    out: List[float] = [0.0] * len(candles)
    for i, c in enumerate(candles):
        if i == 0:
            out[i] = c.high - c.low
        else:
            prev_close = candles[i - 1].close
            out[i] = max(
                c.high - c.low,
                abs(c.high - prev_close),
                abs(c.low - prev_close),
            )
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> List[Optional[float]]:
    """Wilder's Average True Range."""
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    if n < period:
        return out
    tr = true_range(candles)
    seed = sum(tr[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


@dataclass
class BollingerResult:
    upper: List[Optional[float]]
    middle: List[Optional[float]]
    lower: List[Optional[float]]


def bollinger_bands(
    closes: Sequence[float], period: int = 20, std_dev: float = 2.0
) -> BollingerResult:
    n = len(closes)
    upper: List[Optional[float]] = [None] * n
    middle: List[Optional[float]] = [None] * n
    lower: List[Optional[float]] = [None] * n
    for i in range(n):
        if i >= period - 1:
            window = closes[i - period + 1 : i + 1]
            mean = sum(window) / period
            variance = sum((x - mean) ** 2 for x in window) / period
            sd = math.sqrt(variance)
            middle[i] = mean
            upper[i] = mean + std_dev * sd
            lower[i] = mean - std_dev * sd
    return BollingerResult(upper=upper, middle=middle, lower=lower)


@dataclass
class KeltnerResult:
    upper: List[Optional[float]]
    middle: List[Optional[float]]
    lower: List[Optional[float]]


def keltner_channels(
    candles: Sequence[Candle],
    ema_period: int = 20,
    atr_period: int = 10,
    atr_multiplier: float = 2.0,
) -> KeltnerResult:
    closes = [c.close for c in candles]
    mid = ema(closes, ema_period)
    atr_values = atr(candles, atr_period)
    n = len(candles)
    upper: List[Optional[float]] = [None] * n
    lower: List[Optional[float]] = [None] * n
    for i in range(n):
        if mid[i] is not None and atr_values[i] is not None:
            upper[i] = mid[i] + atr_multiplier * atr_values[i]
            lower[i] = mid[i] - atr_multiplier * atr_values[i]
    return KeltnerResult(upper=upper, middle=mid, lower=lower)
