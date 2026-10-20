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


@dataclass
class CandleQuality:
    """Reusable per-candle quality metrics, independent of the named
    pattern classification above. Used as its own scored evidence
    category so the signal engine doesn't rely on pattern-name matching
    alone."""

    body_ratio: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    close_position: float  # 0.0 = closed at the low, 1.0 = closed at the high
    is_pin_bar: bool
    is_outside_bar: bool
    is_inside_bar: bool
    consecutive_bullish: int
    consecutive_bearish: int
    range_vs_avg_ratio: Optional[float]  # current true range / avg of recent ranges
    volatility_state: str  # "COMPRESSED" | "EXPANDED" | "NORMAL" | "UNKNOWN"
    gap: Optional[str]  # "GAP_UP" | "GAP_DOWN" | None
    gap_size_pct: Optional[float]
    rejection_at: Optional[str]  # "support" | "resistance" | None
    false_breakout: Optional[str]  # "support" | "resistance" | None
    quality_score: float  # signed [-1, 1]: bullish/bearish conviction of THIS candle


def _consecutive_direction(candles: Sequence[Candle]) -> tuple[int, int]:
    bullish = 0
    bearish = 0
    for c in reversed(candles):
        if c.is_bullish:
            if bearish > 0:
                break
            bullish += 1
        elif c.is_bearish:
            if bullish > 0:
                break
            bearish += 1
        else:
            break
    return bullish, bearish


def analyze_candle_quality(
    candles: Sequence[Candle],
    sr_levels: Optional[Sequence[SRLevel]] = None,
    range_lookback: int = 10,
    gap_atr: Optional[float] = None,
    level_proximity_pct: float = 0.0015,
) -> CandleQuality:
    """Analyze the most recent candle's quality/conviction.

    ``gap_atr``, if supplied, is used to judge whether an open-vs-prior-close
    discontinuity is large enough to call a genuine gap (vs. normal noise).
    ``sr_levels`` (if supplied) lets this flag wick rejections and false
    breakouts at known levels — using only levels already computed from
    historical data, never inventing one.
    """
    c = candles[-1]
    rng = c.range

    body_ratio = (c.body / rng) if rng > 0 else 0.0
    upper_wick_ratio = (c.upper_wick / rng) if rng > 0 else 0.0
    lower_wick_ratio = (c.lower_wick / rng) if rng > 0 else 0.0
    close_position = ((c.close - c.low) / rng) if rng > 0 else 0.5

    is_pin_bar = (
        rng > 0
        and body_ratio < 0.35
        and (upper_wick_ratio > 0.55 or lower_wick_ratio > 0.55)
    )

    is_outside_bar = False
    is_inside_bar = False
    if len(candles) >= 2:
        prev = candles[-2]
        is_outside_bar = c.high >= prev.high and c.low <= prev.low
        is_inside_bar = c.high <= prev.high and c.low >= prev.low

    consecutive_bullish, consecutive_bearish = _consecutive_direction(candles)

    range_vs_avg_ratio: Optional[float] = None
    volatility_state = "UNKNOWN"
    if len(candles) > range_lookback:
        recent = candles[-(range_lookback + 1) : -1]
        avg_range = sum(x.range for x in recent) / len(recent)
        if avg_range > 0:
            range_vs_avg_ratio = rng / avg_range
            if range_vs_avg_ratio < 0.5:
                volatility_state = "COMPRESSED"
            elif range_vs_avg_ratio > 1.8:
                volatility_state = "EXPANDED"
            else:
                volatility_state = "NORMAL"

    gap: Optional[str] = None
    gap_size_pct: Optional[float] = None
    if len(candles) >= 2 and gap_atr and gap_atr > 0:
        prev_close = candles[-2].close
        gap_distance = c.open - prev_close
        if abs(gap_distance) > gap_atr * 0.5:
            gap = "GAP_UP" if gap_distance > 0 else "GAP_DOWN"
            gap_size_pct = gap_distance / prev_close if prev_close else None

    rejection_at: Optional[str] = None
    false_breakout: Optional[str] = None
    if sr_levels:
        resistance = nearest_level(sr_levels, "resistance")
        support = nearest_level(sr_levels, "support")
        if resistance is not None and resistance.distance_pct <= level_proximity_pct * 3:
            pierced = c.high >= resistance.price
            closed_back_inside = c.close < resistance.price
            if pierced and closed_back_inside and upper_wick_ratio > 0.3:
                false_breakout = "resistance"
            elif upper_wick_ratio > 0.4 and c.close < resistance.price:
                rejection_at = "resistance"
        if support is not None and support.distance_pct <= level_proximity_pct * 3:
            pierced = c.low <= support.price
            closed_back_inside = c.close > support.price
            if pierced and closed_back_inside and lower_wick_ratio > 0.3:
                false_breakout = "support"
            elif lower_wick_ratio > 0.4 and c.close > support.price:
                rejection_at = "support"

    # Composite signed conviction for this single candle.
    parts: List[float] = []
    if is_pin_bar:
        parts.append(0.6 if lower_wick_ratio > upper_wick_ratio else -0.6)
    if c.is_bullish or c.is_bearish:
        base = min(1.0, body_ratio * 1.3) * (1 if c.is_bullish else -1)
        parts.append(base)
    if rejection_at == "support":
        parts.append(0.5)
    elif rejection_at == "resistance":
        parts.append(-0.5)
    if false_breakout == "resistance":
        # Price pierced resistance but failed to hold above it: a trapped-
        # buyers signal that favors continued downside, not a breakout up.
        parts.append(-0.4)
    if false_breakout == "support":
        # Price pierced support but failed to hold below it: a trapped-
        # sellers signal that favors continued upside.
        parts.append(0.4)
    if consecutive_bullish >= 3:
        parts.append(0.3)
    if consecutive_bearish >= 3:
        parts.append(-0.3)

    quality_score = max(-1.0, min(1.0, sum(parts) / len(parts))) if parts else 0.0

    return CandleQuality(
        body_ratio=body_ratio,
        upper_wick_ratio=upper_wick_ratio,
        lower_wick_ratio=lower_wick_ratio,
        close_position=close_position,
        is_pin_bar=is_pin_bar,
        is_outside_bar=is_outside_bar,
        is_inside_bar=is_inside_bar,
        consecutive_bullish=consecutive_bullish,
        consecutive_bearish=consecutive_bearish,
        range_vs_avg_ratio=range_vs_avg_ratio,
        volatility_state=volatility_state,
        gap=gap,
        gap_size_pct=gap_size_pct,
        rejection_at=rejection_at,
        false_breakout=false_breakout,
        quality_score=quality_score,
    )
