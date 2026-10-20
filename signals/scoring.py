"""The seven independent evidence scores and how they combine.

Each score is signed in [-1.0, +1.0]: positive is bullish (CALL) evidence,
negative is bearish (PUT) evidence, and magnitude reflects how strong that
particular piece of evidence is. The composite score is a transparent,
configurable weighted sum — never a single indicator deciding the outcome.
"""
from __future__ import annotations

from typing import List, Optional

from indicators.price_action import nearest_level
from strategy.config import IndicatorConfig, ScoreWeights
from .models import ComponentScore
from .snapshot import IndicatorSnapshot


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def score_trend(snap: IndicatorSnapshot) -> ComponentScore:
    price = snap.last_close
    votes: List[int] = []
    notes: List[str] = []

    ema_values = {p: snap.latest(snap.ema[p]) for p in snap.ema}
    sma_values = {p: snap.latest(snap.sma[p]) for p in snap.sma}

    for p, v in sorted(ema_values.items()):
        if v is not None:
            votes.append(1 if price > v else -1)
            notes.append(f"price {'>' if price > v else '<'} EMA{p}")
    for p, v in sorted(sma_values.items()):
        if v is not None:
            votes.append(1 if price > v else -1)
            notes.append(f"price {'>' if price > v else '<'} SMA{p}")

    ema_sorted_periods = sorted(snap.ema.keys())
    if len(ema_sorted_periods) >= 2:
        vals = [ema_values[p] for p in ema_sorted_periods]
        if all(v is not None for v in vals):
            if all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)):
                votes.append(1)
                votes.append(1)
                notes.append("EMAs stacked bullish (fast>slow)")
            elif all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1)):
                votes.append(-1)
                votes.append(-1)
                notes.append("EMAs stacked bearish (fast<slow)")

    if not votes:
        return ComponentScore("trend", 0.0, 0.0, 0.0, "Insufficient moving-average data.")

    score = _clip(sum(votes) / len(votes))
    detail = "; ".join(notes) if notes else "No clear trend structure."
    return ComponentScore("trend", score, 0.0, 0.0, detail)


def score_momentum(snap: IndicatorSnapshot, cfg: IndicatorConfig) -> ComponentScore:
    rsi_val = snap.latest(snap.rsi_series)
    hist = snap.macd_result.histogram
    hist_val = snap.latest(hist)
    hist_series_valid = [v for v in hist if v is not None]

    parts: List[float] = []
    notes: List[str] = []

    if rsi_val is not None:
        if rsi_val >= cfg.rsi_overbought:
            parts.append(0.3)  # bullish but overextended -> dampened, not negated
            notes.append(f"RSI {rsi_val:.1f} overbought (overextended, caution)")
        elif rsi_val <= cfg.rsi_oversold:
            parts.append(-0.3)
            notes.append(f"RSI {rsi_val:.1f} oversold (overextended, caution)")
        elif rsi_val > 50:
            parts.append(_clip((rsi_val - 50) / 20))
            notes.append(f"RSI {rsi_val:.1f} supportive of upside, not overextended")
        elif rsi_val < 50:
            parts.append(_clip((rsi_val - 50) / 20))
            notes.append(f"RSI {rsi_val:.1f} supportive of downside, not overextended")
        else:
            parts.append(0.0)
            notes.append("RSI neutral at 50")

    if hist_val is not None and len(hist_series_valid) >= 2:
        prev_hist = hist_series_valid[-2]
        rising = hist_val > prev_hist
        if hist_val > 0:
            parts.append(0.6 if rising else 0.3)
            notes.append(f"MACD histogram positive and {'rising' if rising else 'falling'}")
        elif hist_val < 0:
            parts.append(-0.6 if not rising else -0.3)
            notes.append(f"MACD histogram negative and {'falling' if not rising else 'rising'}")
        else:
            notes.append("MACD histogram flat at zero")

    if not parts:
        return ComponentScore("momentum", 0.0, 0.0, 0.0, "Insufficient data for RSI/MACD.")

    score = _clip(sum(parts) / len(parts))
    return ComponentScore("momentum", score, 0.0, 0.0, "; ".join(notes))


def score_volatility(snap: IndicatorSnapshot) -> ComponentScore:
    price = snap.last_close
    upper = snap.latest(snap.bb_result.upper)
    lower = snap.latest(snap.bb_result.lower)
    middle = snap.latest(snap.bb_result.middle)
    k_upper = snap.latest(snap.keltner_result.upper)
    k_lower = snap.latest(snap.keltner_result.lower)

    if None in (upper, lower, middle) or upper == lower:
        return ComponentScore("volatility", 0.0, 0.0, 0.0, "Insufficient data for Bollinger/Keltner.")

    pct_b = (price - lower) / (upper - lower)
    bb_score = _clip((pct_b - 0.5) * 2)

    kelt_score = 0.0
    if k_upper is not None and k_lower is not None and k_upper != k_lower:
        kelt_pct = (price - k_lower) / (k_upper - k_lower)
        kelt_score = _clip((kelt_pct - 0.5) * 2)

    base_score = _clip((bb_score + kelt_score) / 2) if k_upper is not None else bb_score

    ratio, current_bw, avg_bw = snap.bandwidth_regime()
    regime = "normal"
    regime_factor = 1.0
    if ratio < 0.5:
        regime = "squeeze (tight relative to its own recent range)"
        regime_factor = 0.5
    elif ratio > 2.0:
        regime = "expansion (elevated relative to its own recent range)"
        regime_factor = 0.5

    score = _clip(base_score * regime_factor)
    bw_text = f"{current_bw:.4f}" if current_bw is not None else "n/a"
    detail = f"%B={pct_b:.2f}, Bollinger bandwidth={bw_text} ({regime}, {ratio:.2f}x recent average)"
    return ComponentScore("volatility", score, 0.0, 0.0, detail)


def score_price_action(snap: IndicatorSnapshot) -> ComponentScore:
    pattern = snap.candle_pattern
    bias_map = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}
    pattern_score = bias_map.get(pattern.bias, 0.0)

    recent = list(snap.candles[-3:])
    directional_votes = [1 if c.is_bullish else (-1 if c.is_bearish else 0) for c in recent]
    recent_score = _clip(sum(directional_votes) / len(directional_votes)) if directional_votes else 0.0

    score = _clip((pattern_score * 0.7) + (recent_score * 0.3))
    detail = f"Last candle pattern: {pattern.name} ({pattern.bias})"
    return ComponentScore("price_action", score, 0.0, 0.0, detail)


def score_support_resistance(snap: IndicatorSnapshot) -> ComponentScore:
    levels = snap.sr_levels
    if not levels:
        return ComponentScore(
            "support_resistance", 0.0, 0.0, 0.0,
            "No clear swing-based support/resistance levels in available history.",
        )

    resistance = nearest_level(levels, "resistance")
    support = nearest_level(levels, "support")

    proximity_scale = 0.01  # within 1% counts as "near"

    def proximity(level) -> float:
        if level is None:
            return 0.0
        return _clip(1 - (level.distance_pct / proximity_scale), 0.0, 1.0)

    support_push = proximity(support)
    resistance_push = proximity(resistance)
    score = _clip(support_push - resistance_push)

    notes = []
    if support is not None:
        notes.append(f"nearest support ${support.price:.5g} ({support.distance_pct*100:.2f}% away)")
    if resistance is not None:
        notes.append(f"nearest resistance ${resistance.price:.5g} ({resistance.distance_pct*100:.2f}% away)")
    return ComponentScore("support_resistance", score, 0.0, 0.0, "; ".join(notes))


def score_indicator_confirmation(snap: IndicatorSnapshot, cfg: IndicatorConfig) -> ComponentScore:
    votes: List[float] = []
    notes: List[str] = []

    rsi_val = snap.latest(snap.rsi_series)
    if rsi_val is not None:
        votes.append(1.0 if rsi_val > 50 else (-1.0 if rsi_val < 50 else 0.0))
        notes.append(f"RSI {'bullish' if rsi_val > 50 else 'bearish' if rsi_val < 50 else 'neutral'}")

    hist_val = snap.latest(snap.macd_result.histogram)
    if hist_val is not None:
        votes.append(1.0 if hist_val > 0 else (-1.0 if hist_val < 0 else 0.0))
        notes.append(f"MACD {'bullish' if hist_val > 0 else 'bearish' if hist_val < 0 else 'flat'}")

    price = snap.last_close
    upper = snap.latest(snap.bb_result.upper)
    lower = snap.latest(snap.bb_result.lower)
    if upper is not None and lower is not None and upper != lower:
        pct_b = (price - lower) / (upper - lower)
        votes.append(1.0 if pct_b > 0.5 else -1.0)
        notes.append(f"Bollinger {'bullish' if pct_b > 0.5 else 'bearish'} half")

    sar_trend = snap.sar_result.trend[-1] if snap.sar_result.trend else None
    if sar_trend is not None:
        votes.append(1.0 if sar_trend == "up" else -1.0)
        notes.append(f"Parabolic SAR {'bullish' if sar_trend == 'up' else 'bearish'}")

    if not votes:
        return ComponentScore("indicator_confirmation", 0.0, 0.0, 0.0, "Insufficient indicator data.")

    score = _clip(sum(votes) / len(votes))
    return ComponentScore("indicator_confirmation", score, 0.0, 0.0, "; ".join(notes))


def score_multi_timeframe(
    confirmation_snap: Optional[IndicatorSnapshot],
    confirmation_label: Optional[str],
) -> ComponentScore:
    if confirmation_snap is None:
        return ComponentScore(
            "multi_timeframe", 0.0, 0.0, 0.0,
            "No higher-timeframe confirmation data was provided; this score "
            "is neutral rather than fabricated.",
        )

    price = confirmation_snap.last_close
    ema21 = confirmation_snap.latest(confirmation_snap.ema.get(21, []))
    hist_val = confirmation_snap.latest(confirmation_snap.macd_result.histogram)

    votes: List[float] = []
    if ema21 is not None:
        votes.append(1.0 if price > ema21 else -1.0)
    if hist_val is not None:
        votes.append(1.0 if hist_val > 0 else (-1.0 if hist_val < 0 else 0.0))

    if not votes:
        return ComponentScore(
            "multi_timeframe", 0.0, 0.0, 0.0,
            f"Confirmation timeframe ({confirmation_label}) has insufficient indicator data.",
        )

    score = _clip(sum(votes) / len(votes))
    bias = "bullish" if score > 0 else ("bearish" if score < 0 else "neutral")
    return ComponentScore(
        "multi_timeframe", score, 0.0, 0.0,
        f"Confirmation timeframe ({confirmation_label}) bias: {bias}",
    )


def apply_weights(scores: List[ComponentScore], weights: ScoreWeights) -> List[ComponentScore]:
    weight_map = weights.as_dict()
    weighted: List[ComponentScore] = []
    for s in scores:
        w = weight_map.get(s.name, 0.0)
        weighted.append(
            ComponentScore(
                name=s.name,
                score=s.score,
                weight=w,
                weighted_contribution=s.score * w,
                detail=s.detail,
            )
        )
    return weighted


def composite_score(weighted_scores: List[ComponentScore]) -> float:
    """Return the signed composite in [-1, 1]."""
    return _clip(sum(s.weighted_contribution for s in weighted_scores))
