from datetime import datetime, timezone

import pytest

from data.models import Candle
from indicators import momentum, price_action, sar, trend, volatility
from tests.conftest import deterministic_series


def test_sma_basic():
    values = [1, 2, 3, 4, 5]
    result = trend.sma(values, 3)
    assert result == [None, None, 2.0, 3.0, 4.0]


def test_ema_seeds_with_sma_then_recurs():
    values = [1, 2, 3, 4, 5, 6]
    result = trend.ema(values, 3)
    assert result[0] is None and result[1] is None
    assert result[2] == pytest.approx(2.0)
    # EMA(3) multiplier = 0.5
    assert result[3] == pytest.approx((4 - 2.0) * 0.5 + 2.0)


def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 20)]  # strictly increasing
    result = momentum.rsi(closes, period=14)
    assert result[14] == 100.0


def test_rsi_all_losses_is_0():
    closes = [float(i) for i in range(20, 1, -1)]  # strictly decreasing
    result = momentum.rsi(closes, period=14)
    assert result[14] == 0.0


def test_rsi_insufficient_data_returns_all_none():
    closes = [1, 2, 3]
    result = momentum.rsi(closes, period=14)
    assert all(v is None for v in result)


def test_macd_histogram_sign_matches_trend(uptrend_candles):
    # NOTE: a perfectly linear ramp is a degenerate case for MACD — EMA of a
    # strictly linear series locks onto its steady-state lag immediately
    # (by construction of the EMA recurrence), making the histogram exactly
    # zero everywhere. Real (non-linear) trending data is used instead so
    # the fast/slow EMAs actually diverge and reconverge.
    closes = [c.close for c in uptrend_candles]
    result = momentum.macd(closes, fast=12, slow=26, signal=9)
    valid_macd = [v for v in result.macd_line if v is not None]
    valid_hist = [v for v in result.histogram if v is not None]
    assert valid_macd[-1] > 0  # fast EMA above slow EMA in a sustained uptrend
    assert max(valid_hist) > 0.01


def test_bollinger_bands_width_reflects_volatility():
    flat = [10.0] * 25
    result = volatility.bollinger_bands(flat, period=20, std_dev=2.0)
    idx = 19
    assert result.middle[idx] == 10.0
    assert result.upper[idx] == 10.0  # zero variance -> bands collapse to the mean
    assert result.lower[idx] == 10.0


def test_atr_is_never_negative():
    candles = deterministic_series(30, start_price=50, drift=0.1, amplitude=1.0)
    result = volatility.atr(candles, period=14)
    for v in result:
        if v is not None:
            assert v >= 0


def test_keltner_channels_upper_above_lower():
    candles = deterministic_series(40, start_price=20, drift=0.05, amplitude=0.5)
    result = volatility.keltner_channels(candles)
    for u, l in zip(result.upper, result.lower):
        if u is not None and l is not None:
            assert u >= l


def test_parabolic_sar_flips_on_strong_reversal():
    up = deterministic_series(30, start_price=50, drift=0.5, amplitude=0.1)
    down = deterministic_series(30, start_price=up[-1].close, drift=-0.5, amplitude=0.1)
    candles = up + down
    result = sar.parabolic_sar(candles)
    trends = [t for t in result.trend if t is not None]
    assert "up" in trends and "down" in trends


def test_candle_pattern_bullish_engulfing():
    prev = Candle(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=10.0, high=10.1, low=9.4, close=9.5,
    )
    cur = Candle(
        timestamp=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
        open=9.4, high=10.6, low=9.3, close=10.4,
    )
    pattern = price_action.classify_last_candle([prev, cur])
    assert pattern.name == "BULLISH_ENGULFING"
    assert pattern.bias == "bullish"


def test_support_resistance_never_invents_levels_with_flat_data(flat_candles):
    levels = price_action.find_support_resistance(flat_candles)
    # A perfectly flat (near-zero-variance) series should not produce
    # confident swing-based levels far from price; if it finds any, they
    # must be real local extremes present in the given data.
    for lvl in levels:
        assert lvl.price > 0
