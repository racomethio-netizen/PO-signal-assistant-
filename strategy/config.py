"""Configurable strategy parameters: indicator settings, scoring weights,
and the per-timeframe prioritization used by the signal engine.

Nothing here is a magic constant baked into the scoring code — every
weight and threshold is defined here so it can be overridden (e.g. from
the dashboard or API) without touching the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class IndicatorConfig:
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    bb_period: int = 20
    bb_std_dev: float = 2.0

    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0

    sma_periods: Tuple[int, ...] = (20, 50, 200)
    # EMA 200 only populates once >=200 candles are available; it's a
    # harmless no-op (None) otherwise and never blocks a 2m/3m/15m signal.
    ema_periods: Tuple[int, ...] = (9, 21, 50, 200)
    ma_slope_lookback: int = 5

    sar_step: float = 0.02
    sar_max_step: float = 0.2

    atr_period: int = 14
    keltner_ema_period: int = 20
    keltner_atr_period: int = 10
    keltner_atr_multiplier: float = 2.0

    adx_period: int = 14
    adx_strong_trend: float = 25.0  # ADX at/above this = a strong trend
    adx_weak_trend: float = 18.0    # ADX below this = effectively no trend

    stoch_k_period: int = 14
    stoch_d_period: int = 3
    stoch_overbought: float = 80.0
    stoch_oversold: float = 20.0

    williams_period: int = 14
    williams_overbought: float = -20.0
    williams_oversold: float = -80.0

    sr_lookback: int = 3
    sr_cluster_tolerance_pct: float = 0.0015

    candle_quality_range_lookback: int = 10
    candle_quality_level_proximity_pct: float = 0.0015


DEFAULT_INDICATOR_CONFIG = IndicatorConfig()


@dataclass(frozen=True)
class ScoreWeights:
    """Weights for the eight independent evidence scores. Must sum to 1.0
    (validated in ``__post_init__``).

    Note: RSI/MACD/Bollinger/SAR votes are NOT a separate weighted
    category here — they're already distributed across ``momentum``
    (RSI, MACD, Stochastic, Williams %R), ``volatility`` (Bollinger,
    Keltner), and ``market_regime`` (ADX, SAR trend state), so one
    indicator doesn't get counted twice under two different scores.
    """

    trend: float
    momentum: float
    volatility: float
    price_action: float
    support_resistance: float
    multi_timeframe: float
    candle_quality: float
    market_regime: float

    def __post_init__(self) -> None:
        total = (
            self.trend
            + self.momentum
            + self.volatility
            + self.price_action
            + self.support_resistance
            + self.multi_timeframe
            + self.candle_quality
            + self.market_regime
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"ScoreWeights must sum to 1.0, got {total}")

    def as_dict(self) -> Dict[str, float]:
        return {
            "trend": self.trend,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "price_action": self.price_action,
            "support_resistance": self.support_resistance,
            "multi_timeframe": self.multi_timeframe,
            "candle_quality": self.candle_quality,
            "market_regime": self.market_regime,
        }


@dataclass(frozen=True)
class TimeframeConfig:
    name: str
    seconds: int
    min_candles: int
    # Candidate higher timeframes (in seconds) to look for, in priority
    # order, when seeking multi-timeframe confirmation. The engine uses
    # the first one actually supplied by the caller; it never fabricates
    # data for timeframes that weren't given.
    confirmation_timeframes_seconds: Tuple[int, ...]
    weights: ScoreWeights
    # How many bars ahead the hypothetical trade is assumed to expire.
    expiry_bars: int


# --- 2-minute mode ---------------------------------------------------------
# Prioritizes immediate momentum and candle structure/quality above
# broader trend/regime context, matching the 2m profile's focus on
# fast-moving, short-lived setups.
TIMEFRAME_2M = TimeframeConfig(
    name="2m",
    seconds=120,
    min_candles=60,
    confirmation_timeframes_seconds=(180, 300),  # 3m, 5m
    weights=ScoreWeights(
        trend=0.10,
        momentum=0.20,
        volatility=0.10,
        price_action=0.14,
        support_resistance=0.08,
        multi_timeframe=0.08,
        candle_quality=0.22,
        market_regime=0.08,
    ),
    expiry_bars=1,
)

# --- 3-minute mode -----------------------------------------------------
# Balances momentum/trend with candle confirmation and support/resistance,
# plus moderate weight on higher-timeframe confirmation.
TIMEFRAME_3M = TimeframeConfig(
    name="3m",
    seconds=180,
    min_candles=60,
    confirmation_timeframes_seconds=(300, 900),  # 5m, 15m
    weights=ScoreWeights(
        trend=0.15,
        momentum=0.19,
        volatility=0.10,
        price_action=0.13,
        support_resistance=0.12,
        multi_timeframe=0.10,
        candle_quality=0.13,
        market_regime=0.08,
    ),
    expiry_bars=1,
)

# --- 15-minute mode ------------------------------------------------------
# Prioritizes broader trend structure, major support/resistance, and the
# market-regime read (trend strength via ADX) over single-candle quality.
TIMEFRAME_15M = TimeframeConfig(
    name="15m",
    seconds=900,
    min_candles=80,
    confirmation_timeframes_seconds=(1800, 3600),  # 30m, 1h
    weights=ScoreWeights(
        trend=0.20,
        momentum=0.12,
        volatility=0.11,
        price_action=0.06,
        support_resistance=0.16,
        multi_timeframe=0.14,
        candle_quality=0.06,
        market_regime=0.15,
    ),
    expiry_bars=1,
)

TIMEFRAME_CONFIGS: Dict[str, TimeframeConfig] = {
    "2m": TIMEFRAME_2M,
    "3m": TIMEFRAME_3M,
    "15m": TIMEFRAME_15M,
}


def with_weights(cfg: TimeframeConfig, weights: ScoreWeights) -> TimeframeConfig:
    """Return a copy of a timeframe config with different scoring weights."""
    return replace(cfg, weights=weights)


# Minimum composite score (0-100 scale) required to produce a directional
# signal at all. Below this, the engine returns NO TRADE regardless of
# direction. Configurable at runtime via the API/dashboard.
DEFAULT_MIN_SIGNAL_SCORE_THRESHOLD = 35.0

# Label <-> seconds mapping used to look up whichever confirmation
# timeframe candles the caller actually supplied. The engine never invents
# data for a label that wasn't provided.
TIMEFRAME_LABEL_SECONDS: Dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}
SECONDS_TO_LABEL: Dict[int, str] = {v: k for k, v in TIMEFRAME_LABEL_SECONDS.items()}
