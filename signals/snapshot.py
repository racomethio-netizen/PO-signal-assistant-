"""Builds a bundle of all indicator values ("snapshot") for a candle series.

This is the single place where raw candles are turned into indicator
series, so that scoring functions and dashboard "cards" always read from
the same computed values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from data.models import Candle
from indicators import momentum, price_action, sar as sar_module, trend, volatility
from strategy.config import IndicatorConfig


@dataclass
class IndicatorSnapshot:
    candles: Sequence[Candle]
    config: IndicatorConfig

    closes: List[float] = field(default_factory=list)

    sma: Dict[int, List[Optional[float]]] = field(default_factory=dict)
    ema: Dict[int, List[Optional[float]]] = field(default_factory=dict)

    rsi_series: List[Optional[float]] = field(default_factory=list)
    macd_result: momentum.MACDResult = None  # type: ignore[assignment]
    bb_result: volatility.BollingerResult = None  # type: ignore[assignment]
    keltner_result: volatility.KeltnerResult = None  # type: ignore[assignment]
    atr_series: List[Optional[float]] = field(default_factory=list)
    sar_result: sar_module.SARResult = None  # type: ignore[assignment]

    candle_pattern: price_action.CandlePattern = None  # type: ignore[assignment]
    sr_levels: List[price_action.SRLevel] = field(default_factory=list)
    candle_quality: price_action.CandleQuality = None  # type: ignore[assignment]

    adx_result: trend.ADXResult = None  # type: ignore[assignment]
    stochastic_result: momentum.StochasticResult = None  # type: ignore[assignment]
    williams_r_series: List[Optional[float]] = field(default_factory=list)

    bandwidth_series: List[Optional[float]] = field(default_factory=list)

    @property
    def last_close(self) -> float:
        return self.closes[-1]

    def latest(self, series: Sequence[Optional[float]]) -> Optional[float]:
        return trend.latest(series)

    def bandwidth_regime(self, lookback: int = 20) -> "tuple[float, Optional[float], Optional[float]]":
        """Current Bollinger bandwidth relative to its own recent history.

        Returns (ratio, current_bandwidth, average_bandwidth). A ratio near
        1.0 is normal; well below 1.0 is a squeeze; well above 1.0 is an
        expansion/spike — all judged against THIS instrument's own recent
        range rather than an arbitrary absolute percentage, so a strong
        clean trend (which naturally widens bands) isn't mistaken for
        abnormal/choppy volatility.
        """
        valid = [v for v in self.bandwidth_series if v is not None]
        if len(valid) < 6:
            return 1.0, (valid[-1] if valid else None), None
        current = valid[-1]
        history = valid[-(lookback + 1):-1] if len(valid) > lookback else valid[:-1]
        if not history:
            return 1.0, current, None
        avg = sum(history) / len(history)
        ratio = (current / avg) if avg > 0 else 1.0
        return ratio, current, avg


def build_snapshot(candles: Sequence[Candle], config: IndicatorConfig) -> IndicatorSnapshot:
    closes = [c.close for c in candles]

    snap = IndicatorSnapshot(candles=candles, config=config, closes=closes)

    for period in config.sma_periods:
        snap.sma[period] = trend.sma(closes, period)
    for period in config.ema_periods:
        snap.ema[period] = trend.ema(closes, period)

    snap.rsi_series = momentum.rsi(closes, config.rsi_period)
    snap.macd_result = momentum.macd(
        closes, config.macd_fast, config.macd_slow, config.macd_signal
    )
    snap.bb_result = volatility.bollinger_bands(closes, config.bb_period, config.bb_std_dev)
    snap.bandwidth_series = [
        ((u - l) / m) if (u is not None and l is not None and m) else None
        for u, l, m in zip(snap.bb_result.upper, snap.bb_result.lower, snap.bb_result.middle)
    ]
    snap.atr_series = volatility.atr(candles, config.atr_period)
    snap.keltner_result = volatility.keltner_channels(
        candles,
        config.keltner_ema_period,
        config.keltner_atr_period,
        config.keltner_atr_multiplier,
    )
    snap.sar_result = sar_module.parabolic_sar(candles, config.sar_step, config.sar_max_step)
    snap.adx_result = trend.adx(candles, config.adx_period)
    snap.stochastic_result = momentum.stochastic(candles, config.stoch_k_period, config.stoch_d_period)
    snap.williams_r_series = momentum.williams_r(candles, config.williams_period)

    snap.candle_pattern = price_action.classify_last_candle(candles)
    snap.sr_levels = price_action.find_support_resistance(
        candles, config.sr_lookback, config.sr_cluster_tolerance_pct
    )
    snap.candle_quality = price_action.analyze_candle_quality(
        candles,
        snap.sr_levels,
        range_lookback=config.candle_quality_range_lookback,
        gap_atr=snap.latest(snap.atr_series),
        level_proximity_pct=config.candle_quality_level_proximity_pct,
    )

    return snap
