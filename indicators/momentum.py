"""Momentum indicators: RSI (Wilder), MACD, Stochastic, Williams %R."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from data.models import Candle

from .trend import ema, sma


def rsi(closes: Sequence[float], period: int = 14) -> List[Optional[float]]:
    """Wilder's RSI."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < period + 1:
        return out

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change

    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from_averages(avg_gain, avg_loss)

    return out


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass
class MACDResult:
    macd_line: List[Optional[float]]
    signal_line: List[Optional[float]]
    histogram: List[Optional[float]]


def macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> MACDResult:
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)

    macd_line: List[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]

    # Signal line is the EMA of the MACD line, computed over only the
    # contiguous non-None tail (never fabricating values before that).
    first_valid = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: List[Optional[float]] = [None] * len(closes)
    histogram: List[Optional[float]] = [None] * len(closes)
    if first_valid is not None:
        macd_tail = [v for v in macd_line[first_valid:]]  # type: ignore[misc]
        signal_tail = ema(macd_tail, signal)
        for offset, v in enumerate(signal_tail):
            signal_line[first_valid + offset] = v
        for i in range(len(closes)):
            if macd_line[i] is not None and signal_line[i] is not None:
                histogram[i] = macd_line[i] - signal_line[i]

    return MACDResult(macd_line=macd_line, signal_line=signal_line, histogram=histogram)


@dataclass
class StochasticResult:
    percent_k: List[Optional[float]]
    percent_d: List[Optional[float]]


def stochastic(
    candles: Sequence[Candle], k_period: int = 14, d_period: int = 3
) -> StochasticResult:
    """Stochastic oscillator: %K (fast) and %D (SMA of %K)."""
    n = len(candles)
    percent_k: List[Optional[float]] = [None] * n
    for i in range(k_period - 1, n):
        window = candles[i - k_period + 1 : i + 1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        if highest == lowest:
            percent_k[i] = 50.0  # degenerate flat window; neither OB nor OS
        else:
            percent_k[i] = 100.0 * (candles[i].close - lowest) / (highest - lowest)

    first_valid = next((i for i, v in enumerate(percent_k) if v is not None), None)
    percent_d: List[Optional[float]] = [None] * n
    if first_valid is not None:
        tail = [v for v in percent_k[first_valid:]]  # type: ignore[misc]
        d_tail = sma(tail, d_period)
        for offset, v in enumerate(d_tail):
            percent_d[first_valid + offset] = v

    return StochasticResult(percent_k=percent_k, percent_d=percent_d)


def williams_r(candles: Sequence[Candle], period: int = 14) -> List[Optional[float]]:
    """Williams %R, range [-100, 0]. Near 0 = overbought, near -100 = oversold."""
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        window = candles[i - period + 1 : i + 1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        if highest == lowest:
            out[i] = -50.0
        else:
            out[i] = -100.0 * (highest - candles[i].close) / (highest - lowest)
    return out
