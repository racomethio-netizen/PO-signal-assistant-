"""The signal engine: turns validated candles into an explainable Signal.

Orchestration only — all indicator math lives in ``indicators/``, all
scoring lives in ``signals/scoring.py``, all NO-TRADE logic lives in
``signals/filters.py``. This module wires them together and is the single
place that decides the final Direction/Confidence/Score.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from data.models import Candle
from data.validation import validate_candles
from strategy.config import (
    DEFAULT_INDICATOR_CONFIG,
    DEFAULT_MIN_SIGNAL_SCORE_THRESHOLD,
    IndicatorConfig,
    SECONDS_TO_LABEL,
    TimeframeConfig,
)
from . import scoring
from .filters import check_no_trade_filters
from .models import ComponentScore, Confidence, Direction, MarketRegime, Signal, no_trade_signal
from .snapshot import IndicatorSnapshot, build_snapshot

AGREEMENT_SCORE_FLOOR = 0.15  # below this magnitude, a score doesn't count toward agreement


class SignalEngine:
    def __init__(
        self,
        indicator_config: IndicatorConfig = DEFAULT_INDICATOR_CONFIG,
        min_score_threshold: float = DEFAULT_MIN_SIGNAL_SCORE_THRESHOLD,
        llm_provider=None,
    ) -> None:
        self.indicator_config = indicator_config
        self.min_score_threshold = min_score_threshold
        self.llm_provider = llm_provider

    def generate(
        self,
        asset: str,
        timeframe_config: TimeframeConfig,
        candles: Sequence[Candle],
        confirmation_candles_by_label: Optional[Dict[str, Sequence[Candle]]] = None,
        max_stale_seconds: Optional[int] = None,
        explain_with_llm: bool = False,
        require_candle_closed: bool = True,
    ) -> Signal:
        now = datetime.now(timezone.utc)
        confirmation_candles_by_label = confirmation_candles_by_label or {}

        validation = validate_candles(
            candles,
            timeframe_config.seconds,
            min_candles=timeframe_config.min_candles,
            max_stale_seconds=max_stale_seconds,
            require_last_candle_closed=require_candle_closed,
            now=now,
        )
        if not validation.ok:
            error_codes = {e.code for e in validation.errors}
            if "NO_DATA" in error_codes or "STALE_DATA" in error_codes or "LAST_CANDLE_NOT_CLOSED" in error_codes:
                reason = "DATA UNAVAILABLE — NO SIGNAL"
            elif "INSUFFICIENT_CANDLES" in error_codes:
                reason = "INSUFFICIENT DATA — NO SIGNAL"
            else:
                reason = "INSUFFICIENT DATA — NO SIGNAL"
            notes = [e.message for e in validation.errors]
            return no_trade_signal(asset, timeframe_config.name, now, reason, notes)

        data_quality_notes = [w.message for w in validation.warnings]

        snap = build_snapshot(candles, self.indicator_config)

        confirmation_snap, confirmation_label = self._resolve_confirmation(
            timeframe_config, confirmation_candles_by_label
        )
        if confirmation_snap is None:
            data_quality_notes.append(
                "Multi-timeframe confirmation data was not supplied for this request; "
                "the multi-timeframe score is neutral rather than fabricated."
            )

        raw_scores_list = [
            scoring.score_trend(snap, self.indicator_config),
            scoring.score_momentum(snap, self.indicator_config),
            scoring.score_volatility(snap),
            scoring.score_price_action(snap),
            scoring.score_support_resistance(snap),
            scoring.score_candle_quality(snap),
            scoring.score_market_regime(snap, self.indicator_config),
            scoring.score_multi_timeframe(confirmation_snap, confirmation_label),
        ]
        raw_scores = {s.name: s for s in raw_scores_list}

        filter_result = check_no_trade_filters(snap, raw_scores)

        weighted_scores = scoring.apply_weights(raw_scores_list, timeframe_config.weights)
        composite = scoring.composite_score(weighted_scores)
        score_100 = abs(composite) * 100.0

        data_quality_score = self._data_quality_score(validation, confirmation_snap, snap)

        direction, confidence = self._classify(
            composite, score_100, raw_scores_list, filter_result.triggered, data_quality_score,
        )

        market_regime = self._market_regime(snap, raw_scores)

        reasons_supporting, reasons_against = self._build_reasons(
            raw_scores_list, direction, filter_result
        )

        invalidation = self._invalidation_condition(snap, direction)
        expiry_window = self._expiry_window(timeframe_config, direction)

        signal = Signal(
            asset=asset,
            timeframe=timeframe_config.name,
            generated_at=now,
            direction=direction,
            confidence=confidence,
            score=score_100,
            market_regime=market_regime,
            component_scores=weighted_scores,
            trend=self._trend_card(snap, raw_scores["trend"]),
            momentum=self._momentum_card(snap, raw_scores["momentum"]),
            volatility=self._volatility_card(snap, raw_scores["volatility"]),
            support_resistance=self._sr_card(snap, raw_scores["support_resistance"]),
            macd=self._macd_card(snap),
            rsi=self._rsi_card(snap),
            bollinger=self._bollinger_card(snap),
            sar=self._sar_card(snap),
            moving_averages=self._ma_card(snap),
            price_action=self._price_action_card(snap),
            multi_timeframe=self._mtf_card(confirmation_snap, confirmation_label, raw_scores["multi_timeframe"]),
            adx=self._adx_card(snap, raw_scores["market_regime"]),
            stochastic=self._stochastic_card(snap),
            williams_r=self._williams_card(snap),
            candle_quality=self._candle_quality_card(snap, raw_scores["candle_quality"]),
            data_quality_score=data_quality_score,
            reasons_supporting=reasons_supporting,
            reasons_against=reasons_against,
            invalidation_condition=invalidation,
            suggested_expiry_window=expiry_window,
            data_quality_notes=data_quality_notes,
        )

        if explain_with_llm:
            if self.llm_provider is not None:
                try:
                    signal.explanation = self.llm_provider.explain_signal(signal)
                except Exception:
                    signal.explanation = "AI EXPLANATION UNAVAILABLE"
            else:
                signal.explanation = "AI EXPLANATION UNAVAILABLE"

        return signal

    # -- confirmation timeframe resolution ---------------------------------
    def _resolve_confirmation(
        self,
        timeframe_config: TimeframeConfig,
        confirmation_candles_by_label: Dict[str, Sequence[Candle]],
    ):
        for seconds in timeframe_config.confirmation_timeframes_seconds:
            label = SECONDS_TO_LABEL.get(seconds)
            if label and label in confirmation_candles_by_label:
                candles = confirmation_candles_by_label[label]
                validation = validate_candles(candles, seconds, min_candles=30)
                if validation.ok:
                    return build_snapshot(candles, self.indicator_config), label
        return None, None

    # -- classification ------------------------------------------------------
    def _classify(
        self,
        composite: float,
        score_100: float,
        raw_scores: List[ComponentScore],
        filters_triggered: bool,
        data_quality_score: float = 100.0,
    ) -> tuple[Direction, Confidence]:
        if filters_triggered or score_100 < self.min_score_threshold:
            return Direction.NO_TRADE, Confidence.VERY_LOW if filters_triggered else Confidence.LOW

        is_call = composite > 0
        if score_100 >= 70:
            direction = Direction.STRONG_CALL if is_call else Direction.STRONG_PUT
        elif score_100 >= 50:
            direction = Direction.CALL if is_call else Direction.PUT
        else:
            direction = Direction.WEAK_CALL if is_call else Direction.WEAK_PUT

        meaningful = [s for s in raw_scores if abs(s.score) >= AGREEMENT_SCORE_FLOOR]
        if meaningful:
            agreeing = sum(1 for s in meaningful if (s.score > 0) == is_call)
            agreement_ratio = agreeing / len(meaningful)
        else:
            agreement_ratio = 0.5

        # Data quality never changes the technical SCORE (that's purely
        # indicator-derived), but it dampens how much CONFIDENCE we place
        # in it — thinner/staler/less-confirmed data earns a lower label
        # even for an identical score.
        quality_factor = 0.6 + 0.4 * (data_quality_score / 100.0)
        adjusted = score_100 * (0.5 + 0.5 * agreement_ratio) * quality_factor
        if adjusted < 20:
            confidence = Confidence.VERY_LOW
        elif adjusted < 40:
            confidence = Confidence.LOW
        elif adjusted < 60:
            confidence = Confidence.MEDIUM
        elif adjusted < 80:
            confidence = Confidence.HIGH
        else:
            confidence = Confidence.VERY_HIGH

        return direction, confidence

    def _data_quality_score(self, validation, confirmation_snap, snap: IndicatorSnapshot) -> float:
        """Non-directional 0-100 measure of how much to trust this signal's
        underlying data: fewer gaps/warnings, available MTF confirmation,
        and enough history for a confident support/resistance read all
        raise it; their absence lowers it. Never affects the technical
        score itself — see ``_classify``."""
        score = 100.0
        score -= min(30.0, len(validation.warnings) * 10.0)
        if confirmation_snap is None:
            score -= 15.0
        if not snap.sr_levels:
            score -= 10.0
        if snap.candle_quality is not None and snap.candle_quality.volatility_state == "UNKNOWN":
            score -= 10.0
        return max(0.0, min(100.0, score))

    def _market_regime(self, snap: IndicatorSnapshot, raw_scores: Dict[str, ComponentScore]) -> MarketRegime:
        trend_score = raw_scores["trend"].score
        momentum_score = raw_scores["momentum"].score
        ratio, _current_bw, avg_bw = snap.bandwidth_regime()

        if avg_bw is not None and ratio >= 3.0:
            return MarketRegime.VOLATILE_CHOPPY

        adx_val = snap.latest(snap.adx_result.adx) if snap.adx_result else None
        sar_trend_series = snap.sar_result.trend if snap.sar_result else []
        recent_sar = [t for t in sar_trend_series[-3:] if t is not None]
        sar_just_flipped = len(recent_sar) >= 2 and recent_sar[-1] != recent_sar[-2]

        if adx_val is not None:
            if sar_just_flipped and adx_val >= snap.config.adx_weak_trend:
                return MarketRegime.POSSIBLE_REVERSAL
            if adx_val >= snap.config.adx_strong_trend:
                return (
                    MarketRegime.STRONG_TRENDING_BULLISH if trend_score > 0
                    else MarketRegime.STRONG_TRENDING_BEARISH
                )
            if adx_val >= snap.config.adx_weak_trend:
                return (
                    MarketRegime.WEAK_TRENDING_BULLISH if trend_score > 0
                    else MarketRegime.WEAK_TRENDING_BEARISH
                )
            return MarketRegime.RANGING

        # ADX unavailable (not enough history yet) — fall back to the
        # coarser, trend-score-only classification.
        if abs(trend_score) >= 0.4:
            return MarketRegime.TRENDING_BULLISH if trend_score > 0 else MarketRegime.TRENDING_BEARISH
        if abs(trend_score) < 0.25 and abs(momentum_score) < 0.25:
            return MarketRegime.RANGING
        return MarketRegime.UNKNOWN

    def _build_reasons(
        self,
        raw_scores: List[ComponentScore],
        direction: Direction,
        filter_result,
    ) -> tuple[List[str], List[str]]:
        supporting: List[str] = []
        against: List[str] = []

        if direction == Direction.NO_TRADE:
            against.extend(filter_result.reasons)
            for s in raw_scores:
                if abs(s.score) >= AGREEMENT_SCORE_FLOOR:
                    against.append(f"{s.name.replace('_', ' ').title()}: {s.detail}")
            return supporting, against

        is_call = direction.is_call
        for s in raw_scores:
            if abs(s.score) < AGREEMENT_SCORE_FLOOR:
                continue
            label = f"{s.name.replace('_', ' ').title()}: {s.detail}"
            if (s.score > 0) == is_call:
                supporting.append(label)
            else:
                against.append(label)

        if not against:
            against.append("No significant contrary evidence found in the available data.")

        return supporting, against

    def _invalidation_condition(self, snap: IndicatorSnapshot, direction: Direction) -> str:
        if direction == Direction.NO_TRADE:
            return "N/A — no directional signal was generated."

        from indicators.price_action import nearest_level

        if direction.is_call:
            level = nearest_level(snap.sr_levels, "resistance")
            level_clause = (
                f"price closes back below ${level.price:.5g}" if level is not None
                else "price loses the recent higher-timeframe structure"
            )
            return (
                f"This bullish case is invalidated if {level_clause}, RSI drops back "
                "below 50 with a bearish MACD cross, or the Parabolic SAR flips bearish."
            )
        else:
            level = nearest_level(snap.sr_levels, "support")
            level_clause = (
                f"price closes back above ${level.price:.5g}" if level is not None
                else "price reclaims the recent higher-timeframe structure"
            )
            return (
                f"This bearish case is invalidated if {level_clause}, RSI rises back "
                "above 50 with a bullish MACD cross, or the Parabolic SAR flips bullish."
            )

    def _expiry_window(self, timeframe_config: TimeframeConfig, direction: Direction) -> str:
        if direction == Direction.NO_TRADE:
            return "N/A"
        total_seconds = timeframe_config.seconds * timeframe_config.expiry_bars
        minutes = total_seconds / 60
        return f"~{minutes:.0f} minute(s) ({timeframe_config.expiry_bars} candle(s) at {timeframe_config.name})"

    # -- dashboard "cards" ----------------------------------------------------
    def _trend_card(self, snap: IndicatorSnapshot, score: ComponentScore) -> Dict:
        return {
            "score": round(score.score, 3),
            "detail": score.detail,
            "ema": {p: snap.latest(s) for p, s in snap.ema.items()},
            "sma": {p: snap.latest(s) for p, s in snap.sma.items()},
        }

    def _momentum_card(self, snap: IndicatorSnapshot, score: ComponentScore) -> Dict:
        return {"score": round(score.score, 3), "detail": score.detail}

    def _volatility_card(self, snap: IndicatorSnapshot, score: ComponentScore) -> Dict:
        return {
            "score": round(score.score, 3),
            "detail": score.detail,
            "atr": snap.latest(snap.atr_series),
        }

    def _sr_card(self, snap: IndicatorSnapshot, score: ComponentScore) -> Dict:
        return {
            "score": round(score.score, 3),
            "detail": score.detail,
            "levels": [
                {
                    "price": lvl.price,
                    "kind": lvl.kind,
                    "touches": lvl.touches,
                    "distance_pct": round(lvl.distance_pct * 100, 4),
                }
                for lvl in snap.sr_levels
            ],
        }

    def _macd_card(self, snap: IndicatorSnapshot) -> Dict:
        return {
            "macd": snap.latest(snap.macd_result.macd_line),
            "signal": snap.latest(snap.macd_result.signal_line),
            "histogram": snap.latest(snap.macd_result.histogram),
        }

    def _rsi_card(self, snap: IndicatorSnapshot) -> Dict:
        return {"value": snap.latest(snap.rsi_series), "period": snap.config.rsi_period}

    def _bollinger_card(self, snap: IndicatorSnapshot) -> Dict:
        return {
            "upper": snap.latest(snap.bb_result.upper),
            "middle": snap.latest(snap.bb_result.middle),
            "lower": snap.latest(snap.bb_result.lower),
        }

    def _sar_card(self, snap: IndicatorSnapshot) -> Dict:
        return {
            "value": snap.latest(snap.sar_result.values),
            "trend": snap.sar_result.trend[-1] if snap.sar_result.trend else None,
        }

    def _ma_card(self, snap: IndicatorSnapshot) -> Dict:
        return {
            "ema": {p: snap.latest(s) for p, s in snap.ema.items()},
            "sma": {p: snap.latest(s) for p, s in snap.sma.items()},
        }

    def _price_action_card(self, snap: IndicatorSnapshot) -> Dict:
        return {
            "pattern": snap.candle_pattern.name,
            "bias": snap.candle_pattern.bias,
        }

    def _mtf_card(self, confirmation_snap, label, score: ComponentScore) -> Dict:
        return {
            "confirmation_timeframe": label,
            "available": confirmation_snap is not None,
            "score": round(score.score, 3),
            "detail": score.detail,
        }

    def _adx_card(self, snap: IndicatorSnapshot, score: ComponentScore) -> Dict:
        return {
            "score": round(score.score, 3),
            "detail": score.detail,
            "adx": snap.latest(snap.adx_result.adx) if snap.adx_result else None,
            "plus_di": snap.latest(snap.adx_result.plus_di) if snap.adx_result else None,
            "minus_di": snap.latest(snap.adx_result.minus_di) if snap.adx_result else None,
        }

    def _stochastic_card(self, snap: IndicatorSnapshot) -> Dict:
        if not snap.stochastic_result:
            return {"percent_k": None, "percent_d": None}
        return {
            "percent_k": snap.latest(snap.stochastic_result.percent_k),
            "percent_d": snap.latest(snap.stochastic_result.percent_d),
        }

    def _williams_card(self, snap: IndicatorSnapshot) -> Dict:
        return {"value": snap.latest(snap.williams_r_series) if snap.williams_r_series else None}

    def _candle_quality_card(self, snap: IndicatorSnapshot, score: ComponentScore) -> Dict:
        cq = snap.candle_quality
        if cq is None:
            return {"score": round(score.score, 3), "detail": score.detail}
        return {
            "score": round(score.score, 3),
            "detail": score.detail,
            "body_ratio": round(cq.body_ratio, 3),
            "upper_wick_ratio": round(cq.upper_wick_ratio, 3),
            "lower_wick_ratio": round(cq.lower_wick_ratio, 3),
            "close_position": round(cq.close_position, 3),
            "is_pin_bar": cq.is_pin_bar,
            "is_outside_bar": cq.is_outside_bar,
            "is_inside_bar": cq.is_inside_bar,
            "consecutive_bullish": cq.consecutive_bullish,
            "consecutive_bearish": cq.consecutive_bearish,
            "volatility_state": cq.volatility_state,
            "gap": cq.gap,
            "rejection_at": cq.rejection_at,
            "false_breakout": cq.false_breakout,
        }
