"""The seven independent evidence scores and how they combine.

Each score is signed in [-1.0, +1.0]: positive is bullish (CALL) evidence,
negative is bearish (PUT) evidence, and magnitude reflects how strong that
particular piece of evidence is. The composite score is a transparent,
configurable weighted sum — never a single indicator deciding the outcome.
"""
from __future__ import annotations

from typing import List, Optional

from indicators import trend as trend_mod
from indicators.price_action import nearest_level
from strategy.config import IndicatorConfig, ScoreWeights
from .models import ComponentScore
from .snapshot import IndicatorSnapshot


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def score_trend(snap: IndicatorSnapshot, cfg: Optional[IndicatorConfig] = None) -> ComponentScore:
    price = snap.last_close
    votes: List[float] = []
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

    # Trend acceleration/deceleration: is the fastest EMA still gaining pace?
    fast_period = min(snap.ema.keys()) if snap.ema else None
    if fast_period is not None:
        lookback = cfg.ma_slope_lookback if cfg else 5
        ma_slope = trend_mod.slope(snap.ema[fast_period], lookback)
        if ma_slope is not None and abs(ma_slope) > 1e-6:
            votes.append(_clip(ma_slope * 50))
            notes.append(f"EMA{fast_period} slope {'rising' if ma_slope > 0 else 'falling'} ({ma_slope*100:.3f}%/bar)")

    if not votes:
        return ComponentScore("trend", 0.0, 0.0, 0.0, "Insufficient moving-average data.")

    score = _clip(sum(votes) / len(votes))
    detail = "; ".join(notes) if notes else "No clear trend structure."
    return ComponentScore("trend", score, 0.0, 0.0, detail)


def score_momentum(snap: IndicatorSnapshot, cfg: IndicatorConfig) -> ComponentScore:
    """Momentum evidence from RSI, MACD histogram, Stochastic, and Williams
    %R together. These four are deliberately averaged into ONE category
    (not four separately-weighted ones) since they all measure the same
    underlying thing — momentum/overbought-oversold — just via different
    math; weighting them separately would let momentum dominate the
    composite purely by indicator count."""
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

    stoch_k = snap.latest(snap.stochastic_result.percent_k) if snap.stochastic_result else None
    if stoch_k is not None:
        if stoch_k >= cfg.stoch_overbought:
            parts.append(0.2)
            notes.append(f"Stochastic %K {stoch_k:.1f} overbought (overextended, caution)")
        elif stoch_k <= cfg.stoch_oversold:
            parts.append(-0.2)
            notes.append(f"Stochastic %K {stoch_k:.1f} oversold (overextended, caution)")
        else:
            parts.append(_clip((stoch_k - 50) / 30))
            notes.append(f"Stochastic %K {stoch_k:.1f}")

    wr = snap.latest(snap.williams_r_series) if snap.williams_r_series else None
    if wr is not None:
        if wr >= cfg.williams_overbought:
            parts.append(0.2)
            notes.append(f"Williams %R {wr:.1f} overbought (overextended, caution)")
        elif wr <= cfg.williams_oversold:
            parts.append(-0.2)
            notes.append(f"Williams %R {wr:.1f} oversold (overextended, caution)")
        else:
            parts.append(_clip((wr + 50) / 30))
            notes.append(f"Williams %R {wr:.1f}")

    if not parts:
        return ComponentScore("momentum", 0.0, 0.0, 0.0, "Insufficient data for RSI/MACD/Stochastic/Williams %R.")

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


def score_market_regime(snap: IndicatorSnapshot, cfg: IndicatorConfig) -> ComponentScore:
    """Evidence from the market's REGIME, not raw price level: how strong is
    the prevailing trend (ADX), which way is it pointed (+DI/-DI), and has
    Parabolic SAR just flipped (a possible-reversal warning that dampens,
    rather than reinforces, continuation evidence)? Deliberately the only
    place ADX and SAR vote, so they don't also get counted inside `trend`
    or `momentum`."""
    adx_val = snap.latest(snap.adx_result.adx) if snap.adx_result else None
    plus_di = snap.latest(snap.adx_result.plus_di) if snap.adx_result else None
    minus_di = snap.latest(snap.adx_result.minus_di) if snap.adx_result else None
    sar_trend_series = snap.sar_result.trend if snap.sar_result else []
    sar_trend = sar_trend_series[-1] if sar_trend_series else None

    notes: List[str] = []

    if adx_val is None or plus_di is None or minus_di is None:
        return ComponentScore("market_regime", 0.0, 0.0, 0.0, "Insufficient data for ADX/+DI/-DI.")

    direction = 1.0 if plus_di > minus_di else (-1.0 if minus_di > plus_di else 0.0)

    if adx_val >= cfg.adx_strong_trend:
        strength_factor = 1.0
        notes.append(f"ADX {adx_val:.1f} — strong trend")
    elif adx_val >= cfg.adx_weak_trend:
        strength_factor = 0.5
        notes.append(f"ADX {adx_val:.1f} — weak/developing trend")
    else:
        strength_factor = 0.15
        notes.append(f"ADX {adx_val:.1f} — ranging/no trend")

    notes.append(f"+DI {plus_di:.1f} {'>' if plus_di > minus_di else '<'} -DI {minus_di:.1f}")

    score = direction * strength_factor

    # Possible reversal: SAR flipped within the last 2 candles while the
    # ADX-implied direction still reflects the OLD trend — a caution flag
    # that dampens (not reverses) the regime vote rather than overriding it.
    recent_sar = [t for t in sar_trend_series[-3:] if t is not None]
    sar_just_flipped = len(recent_sar) >= 2 and recent_sar[-1] != recent_sar[-2]
    if sar_just_flipped:
        score *= 0.4
        notes.append(f"Parabolic SAR just flipped {sar_trend} — possible reversal, regime evidence dampened")
    elif sar_trend is not None:
        sar_agrees = (sar_trend == "up" and direction > 0) or (sar_trend == "down" and direction < 0)
        if not sar_agrees and direction != 0:
            score *= 0.6
            notes.append(f"Parabolic SAR ({sar_trend}) disagrees with +DI/-DI direction — dampened")
        else:
            notes.append(f"Parabolic SAR ({sar_trend}) agrees with regime direction")

    return ComponentScore("market_regime", _clip(score), 0.0, 0.0, "; ".join(notes))


def score_candle_quality(snap: IndicatorSnapshot) -> ComponentScore:
    """Evidence from THIS candle's micro-structure: body/wick ratios, pin
    bars, consecutive-candle momentum, and rejection/false-breakout at a
    nearby level. Distinct from `price_action` (named pattern + 3-candle
    direction) — this is about how convincingly the most recent candle was
    built, not what pattern it resembles."""
    cq = snap.candle_quality
    if cq is None:
        return ComponentScore("candle_quality", 0.0, 0.0, 0.0, "Insufficient data for candle-quality analysis.")

    notes: List[str] = [f"body ratio {cq.body_ratio:.2f}, close position {cq.close_position:.2f}"]
    if cq.is_pin_bar:
        notes.append("pin bar")
    if cq.is_outside_bar:
        notes.append("outside bar")
    if cq.consecutive_bullish >= 3:
        notes.append(f"{cq.consecutive_bullish} consecutive bullish candles")
    if cq.consecutive_bearish >= 3:
        notes.append(f"{cq.consecutive_bearish} consecutive bearish candles")
    if cq.volatility_state != "UNKNOWN":
        notes.append(f"range {cq.volatility_state.lower()} vs recent average")
    if cq.gap:
        notes.append(f"{cq.gap} ({(cq.gap_size_pct or 0)*100:.2f}%)")
    if cq.rejection_at:
        notes.append(f"rejection at {cq.rejection_at}")
    if cq.false_breakout:
        notes.append(f"false breakout at {cq.false_breakout}")

    return ComponentScore("candle_quality", _clip(cq.quality_score), 0.0, 0.0, "; ".join(notes))


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
