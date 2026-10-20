"""Parabolic SAR (Stop and Reverse)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from data.models import Candle


@dataclass
class SARResult:
    values: List[Optional[float]]
    # "up" = SAR below price (bullish), "down" = SAR above price (bearish)
    trend: List[Optional[str]]


def parabolic_sar(
    candles: Sequence[Candle],
    step: float = 0.02,
    max_step: float = 0.2,
) -> SARResult:
    n = len(candles)
    values: List[Optional[float]] = [None] * n
    trend: List[Optional[str]] = [None] * n
    if n < 2:
        return SARResult(values=values, trend=trend)

    is_uptrend = candles[1].close >= candles[0].close
    af = step
    ep = candles[1].high if is_uptrend else candles[1].low
    sar = candles[0].low if is_uptrend else candles[0].high

    values[0] = None
    values[1] = sar
    trend[1] = "up" if is_uptrend else "down"

    for i in range(2, n):
        prev_sar = sar
        sar = prev_sar + af * (ep - prev_sar)

        if is_uptrend:
            sar = min(sar, candles[i - 1].low, candles[i - 2].low)
            if candles[i].low < sar:
                is_uptrend = False
                sar = ep
                ep = candles[i].low
                af = step
            else:
                if candles[i].high > ep:
                    ep = candles[i].high
                    af = min(af + step, max_step)
        else:
            sar = max(sar, candles[i - 1].high, candles[i - 2].high)
            if candles[i].high > sar:
                is_uptrend = True
                sar = ep
                ep = candles[i].high
                af = step
            else:
                if candles[i].low < ep:
                    ep = candles[i].low
                    af = min(af + step, max_step)

        values[i] = sar
        trend[i] = "up" if is_uptrend else "down"

    return SARResult(values=values, trend=trend)
