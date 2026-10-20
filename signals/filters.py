"""NO-TRADE filters.

These run independently of (and before) the weighted composite score, so
that clearly unsuitable conditions are blocked even if a threshold-based
score might otherwise slip through. NO TRADE is always preferred over a
directional call when evidence is genuinely conflicting or ambiguous.
"""
from __future__ import annotations

from typing import Dict, List

from indicators.price_action import nearest_level
from .models import ComponentScore, NoTradeFilterResult
from .snapshot import IndicatorSnapshot

AT_LEVEL_DISTANCE_PCT = 0.002          # within 0.2% counts as "directly at" a level
ABNORMAL_BANDWIDTH_RATIO = 3.0         # current bandwidth vs its own recent average = abnormal
SIDEWAYS_SCORE_THRESHOLD = 0.12        # |score| below this counts as "no clear structure"
SIDEWAYS_BANDWIDTH_RATIO = 0.35        # squeeze relative to recent average, for the sideways filter
STRONG_CONFLICT_THRESHOLD = 0.4

AMBIGUOUS_PATTERNS = {"DOJI", "INSIDE_BAR", "FLAT"}


def check_no_trade_filters(
    snap: IndicatorSnapshot,
    raw_scores: Dict[str, ComponentScore],
) -> NoTradeFilterResult:
    reasons: List[str] = []

    trend = raw_scores.get("trend")
    momentum = raw_scores.get("momentum")
    price_action_score = raw_scores.get("price_action")

    # 1. Indicators strongly conflict (trend vs momentum pulling opposite ways).
    if trend and momentum:
        if (
            abs(trend.score) >= STRONG_CONFLICT_THRESHOLD
            and abs(momentum.score) >= STRONG_CONFLICT_THRESHOLD
            and (trend.score > 0) != (momentum.score > 0)
        ):
            reasons.append(
                f"Trend ({trend.score:+.2f}) and momentum ({momentum.score:+.2f}) "
                "strongly conflict."
            )

    # 2. Ambiguous candle structure with no offsetting trend conviction.
    if snap.candle_pattern.name in AMBIGUOUS_PATTERNS and trend and abs(trend.score) < 0.25:
        reasons.append(f"Candle structure is ambiguous ({snap.candle_pattern.name}).")

    # 3. Price sitting directly at a major support/resistance level.
    resistance = nearest_level(snap.sr_levels, "resistance")
    support = nearest_level(snap.sr_levels, "support")
    for lvl, kind in ((resistance, "resistance"), (support, "support")):
        if lvl is not None and lvl.distance_pct <= AT_LEVEL_DISTANCE_PCT:
            reasons.append(
                f"Price is directly at a major {kind} level (${lvl.price:.5g}, "
                f"{lvl.distance_pct*100:.3f}% away)."
            )

    # 4. Abnormal volatility: current Bollinger bandwidth vs this instrument's
    # own recent average (NOT an absolute percentage, so a strong clean trend
    # that naturally widens bands isn't mistaken for abnormal/choppy volatility).
    ratio, current_bw, avg_bw = snap.bandwidth_regime()
    if avg_bw is not None and ratio >= ABNORMAL_BANDWIDTH_RATIO:
        reasons.append(
            f"Abnormal volatility: Bollinger bandwidth is {ratio:.1f}x its recent "
            f"average ({current_bw:.5f} vs avg {avg_bw:.5f})."
        )

    # 5. Sideways market without clear structure (everything near zero + a squeeze).
    if avg_bw is not None and trend and momentum and price_action_score:
        all_flat = (
            abs(trend.score) < SIDEWAYS_SCORE_THRESHOLD
            and abs(momentum.score) < SIDEWAYS_SCORE_THRESHOLD
            and abs(price_action_score.score) < SIDEWAYS_SCORE_THRESHOLD
        )
        if all_flat and ratio <= SIDEWAYS_BANDWIDTH_RATIO:
            reasons.append(
                "Market is moving sideways in a tight range with no clear directional "
                "structure."
            )

    return NoTradeFilterResult(triggered=len(reasons) > 0, reasons=reasons)
