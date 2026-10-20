"""Candle structure classification and support/resistance detection.

Support/resistance levels are derived strictly from swing highs/lows
present in the supplied candle history — never invented or assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from data.models import Candle


@dataclass
class CandlePattern:
    name: str
    bias: str  # "bullish" | "bearish" | "neutral"


def classify_last_candle(candles: Sequence[Candle]) -> CandlePattern:
    """Classify the most recent 1-2 candles into a simple, named pattern."""
    if len(candles) < 1:
        return CandlePattern(name="UNKNOWN", bias="neutral")

    c = candles[-1]
    rng = c.range
    if rng == 0:
        return CandlePattern(name="FLAT", bias="neutral")

    body_ratio = c.body / rng

    # Doji: very small body relative to range.
    if body_ratio < 0.1:
        return CandlePattern(name="DOJI", bias="neutral")

    # Marubozu: body dominates the range, almost no wicks.
    if body_ratio > 0.9:
        return CandlePattern(
            name="BULLISH_MARUBOZU" if c.is_bullish else "BEARISH_MARUBOZU",
            bias="bullish" if c.is_bullish else "bearish",
        )

    # Hammer / Shooting star: small body, long opposite wick.
    if c.lower_wick > 2 * c.body and c.upper_wick < c.body:
        return CandlePattern(name="HAMMER", bias="bullish")
    if c.upper_wick > 2 * c.body and c.lower_wick < c.body:
        return CandlePattern(name="SHOOTING_STAR", bias="bearish")

    if len(candles) >= 2:
        prev = candles[-2]
        # Engulfing patterns.
        if (
            c.is_bullish
            and prev.is_bearish
            and c.close >= prev.open
            and c.open <= prev.close
        ):
            return CandlePattern(name="BULLISH_ENGULFING", bias="bullish")
        if (
            c.is_bearish
            and prev.is_bullish
            and c.close <= prev.open
            and c.open >= prev.close
        ):
            return CandlePattern(name="BEARISH_ENGULFING", bias="bearish")
        # Inside bar: ambiguous / consolidation.
        if c.high <= prev.high and c.low >= prev.low:
            return CandlePattern(name="INSIDE_BAR", bias="neutral")

    return CandlePattern(
        name="BULLISH_CANDLE" if c.is_bullish else "BEARISH_CANDLE",
        bias="bullish" if c.is_bullish else "bearish",
    )


@dataclass
class SRLevel:
    price: float
    kind: str  # "support" | "resistance"
    touches: int
    distance: float  # absolute distance from the reference price
    distance_pct: float


def _swing_points(candles: Sequence[Candle], lookback: int) -> tuple[List[int], List[int]]:
    """Return indices of swing highs and swing lows using a symmetric window."""
    highs_idx: List[int] = []
    lows_idx: List[int] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        window = candles[i - lookback : i + lookback + 1]
        if candles[i].high == max(c.high for c in window):
            highs_idx.append(i)
        if candles[i].low == min(c.low for c in window):
            lows_idx.append(i)
    return highs_idx, lows_idx


def _cluster_levels(prices: List[float], reference_price: float, tolerance_pct: float) -> List[tuple[float, int]]:
    """Cluster nearby price levels together, returning (price, touch_count)."""
    if not prices:
        return []
    prices = sorted(prices)
    clusters: List[List[float]] = [[prices[0]]]
    for p in prices[1:]:
        last_cluster_mean = sum(clusters[-1]) / len(clusters[-1])
        if abs(p - last_cluster_mean) / max(reference_price, 1e-9) <= tolerance_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [(sum(cluster) / len(cluster), len(cluster)) for cluster in clusters]


def find_support_resistance(
    candles: Sequence[Candle],
    lookback: int = 3,
    cluster_tolerance_pct: float = 0.0015,
    max_levels: int = 3,
) -> List[SRLevel]:
    """Find nearby support and resistance levels from swing highs/lows.

    Returns up to ``max_levels`` nearest support levels and ``max_levels``
    nearest resistance levels relative to the last close, sorted by
    distance (nearest first). Returns an empty list if there is not
    enough history to compute any swing points.
    """
    n = len(candles)
    if n < lookback * 2 + 3:
        return []

    reference_price = candles[-1].close
    highs_idx, lows_idx = _swing_points(candles, lookback)

    resistance_prices = [candles[i].high for i in highs_idx if candles[i].high > reference_price]
    support_prices = [candles[i].low for i in lows_idx if candles[i].low < reference_price]

    resistance_clusters = _cluster_levels(resistance_prices, reference_price, cluster_tolerance_pct)
    support_clusters = _cluster_levels(support_prices, reference_price, cluster_tolerance_pct)

    levels: List[SRLevel] = []
    for price, touches in sorted(resistance_clusters, key=lambda x: x[0])[:max_levels]:
        dist = price - reference_price
        levels.append(
            SRLevel(
                price=price,
                kind="resistance",
                touches=touches,
                distance=dist,
                distance_pct=dist / reference_price if reference_price else 0.0,
            )
        )
    for price, touches in sorted(support_clusters, key=lambda x: x[0], reverse=True)[:max_levels]:
        dist = reference_price - price
        levels.append(
            SRLevel(
                price=price,
                kind="support",
                touches=touches,
                distance=dist,
                distance_pct=dist / reference_price if reference_price else 0.0,
            )
        )

    levels.sort(key=lambda lvl: lvl.distance)
    return levels


def nearest_level(levels: Sequence[SRLevel], kind: str) -> Optional[SRLevel]:
    candidates = [lvl for lvl in levels if lvl.kind == kind]
    if not candidates:
        return None
    return min(candidates, key=lambda lvl: lvl.distance)
