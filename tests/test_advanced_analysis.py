"""Tests for the Phase 2-6 advanced-analysis upgrade: ADX/+DI/-DI,
Stochastic, Williams %R, candle-quality analysis, market-regime scoring,
candle-closed confirmation, EV formula, and confidence calibration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backtest.metrics import confidence_calibration, expected_value_per_stake
from backtest.models import BacktestTrade
from data.models import Candle
from data.validation import is_last_candle_closed, validate_candles
from indicators import momentum, price_action, trend
from signals.engine import SignalEngine
from signals.models import Confidence, Direction, MarketRegime
from strategy.config import TIMEFRAME_CONFIGS
from tests.conftest import deterministic_series

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


# --- ADX / +DI / -DI ------------------------------------------------------
def test_adx_high_in_clean_uptrend(uptrend_candles):
    result = trend.adx(uptrend_candles, period=14)
    adx_val = trend.latest(result.adx)
    plus_di = trend.latest(result.plus_di)
    minus_di = trend.latest(result.minus_di)
    assert adx_val is not None
    assert adx_val > 25  # strong trend
    assert plus_di > minus_di  # bullish directional dominance


def test_adx_low_in_flat_market(flat_candles):
    result = trend.adx(flat_candles, period=14)
    adx_val = trend.latest(result.adx)
    # A near-flat market shouldn't register as a strong trend.
    assert adx_val is None or adx_val < 25


def test_adx_insufficient_data_returns_none():
    candles = deterministic_series(10, start_price=50, drift=0.1, amplitude=0.1)
    result = trend.adx(candles, period=14)
    assert all(v is None for v in result.adx)


def test_ma_slope_positive_in_uptrend(uptrend_candles):
    closes = [c.close for c in uptrend_candles]
    ema9 = trend.ema(closes, 9)
    s = trend.slope(ema9, lookback=5)
    assert s is not None and s > 0


def test_ma_separation_widens_in_strong_trend(uptrend_candles):
    closes = [c.close for c in uptrend_candles]
    ema9 = trend.ema(closes, 9)
    ema21 = trend.ema(closes, 21)
    sep = trend.separation(ema9, ema21)
    assert sep is not None and sep > 0  # fast EMA above slow in an uptrend


# --- Stochastic / Williams %R ---------------------------------------------
def test_stochastic_overbought_in_strong_uptrend(uptrend_candles):
    result = momentum.stochastic(uptrend_candles, k_period=14, d_period=3)
    k = trend.latest(result.percent_k)
    assert k is not None and k > 70


def test_stochastic_bounds():
    candles = deterministic_series(40, start_price=50, drift=0.2, amplitude=0.3)
    result = momentum.stochastic(candles, 14, 3)
    for v in result.percent_k:
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_williams_r_bounds_and_overbought(uptrend_candles):
    wr = momentum.williams_r(uptrend_candles, period=14)
    valid = [v for v in wr if v is not None]
    assert valid
    for v in valid:
        assert -100.0 <= v <= 0.0
    assert valid[-1] > -20  # overbought territory in a strong uptrend


# --- Candle quality ---------------------------------------------------------
def test_candle_quality_body_and_wick_ratios_sum_sane():
    c = Candle(timestamp=T0, open=10.0, high=10.5, low=9.5, close=10.3)
    cq = price_action.analyze_candle_quality([c])
    assert 0 <= cq.body_ratio <= 1
    assert 0 <= cq.upper_wick_ratio <= 1
    assert 0 <= cq.lower_wick_ratio <= 1
    assert 0 <= cq.close_position <= 1


def test_candle_quality_pin_bar_detection():
    # Small body near the top, long lower wick -> hammer-like pin bar.
    c = Candle(timestamp=T0, open=10.0, high=10.05, low=9.0, close=10.03)
    cq = price_action.analyze_candle_quality([c])
    assert cq.is_pin_bar
    assert cq.quality_score > 0  # bullish pin bar (long lower wick)


def test_candle_quality_outside_bar():
    prev = Candle(timestamp=T0, open=10.0, high=10.2, low=9.8, close=10.1)
    cur = Candle(timestamp=T0 + timedelta(minutes=2), open=9.5, high=10.5, low=9.4, close=10.3)
    cq = price_action.analyze_candle_quality([prev, cur])
    assert cq.is_outside_bar
    assert not cq.is_inside_bar


def test_candle_quality_consecutive_momentum():
    candles = []
    price = 10.0
    for i in range(5):
        c = Candle(timestamp=T0 + timedelta(minutes=2 * i), open=price, high=price + 0.5, low=price - 0.1, close=price + 0.4)
        candles.append(c)
        price = c.close
    cq = price_action.analyze_candle_quality(candles)
    assert cq.consecutive_bullish == 5
    assert cq.consecutive_bearish == 0


def test_candle_quality_gap_detection():
    prev = Candle(timestamp=T0, open=10.0, high=10.1, low=9.9, close=10.0)
    gapped = Candle(timestamp=T0 + timedelta(minutes=2), open=10.5, high=10.6, low=10.4, close=10.55)
    cq = price_action.analyze_candle_quality([prev, gapped], gap_atr=0.05)
    assert cq.gap == "GAP_UP"
    assert cq.gap_size_pct is not None and cq.gap_size_pct > 0


def test_candle_quality_no_gap_without_atr_context():
    prev = Candle(timestamp=T0, open=10.0, high=10.1, low=9.9, close=10.0)
    small_move = Candle(timestamp=T0 + timedelta(minutes=2), open=10.01, high=10.1, low=9.95, close=10.05)
    cq = price_action.analyze_candle_quality([prev, small_move], gap_atr=0.5)
    assert cq.gap is None  # move is well within normal ATR-scaled noise


def test_candle_quality_false_breakout_at_resistance():
    # Build a series with a clear resistance, then a candle that pierces it
    # intrabar but closes back below.
    base = deterministic_series(60, start_price=100.0, drift=0.0, amplitude=0.3)
    levels = price_action.find_support_resistance(base)
    resistance = price_action.nearest_level(levels, "resistance")
    if resistance is None:
        pytest.skip("deterministic fixture didn't produce a resistance level")
    pierce = Candle(
        timestamp=base[-1].timestamp + timedelta(minutes=2),
        open=resistance.price - 0.2, high=resistance.price + 0.3,
        low=resistance.price - 0.3, close=resistance.price - 0.15,
    )
    candles = base + [pierce]
    levels2 = price_action.find_support_resistance(candles)
    cq = price_action.analyze_candle_quality(candles, levels2, gap_atr=0.0)
    # Either a false breakout or a rejection is a reasonable read; the
    # key invariant is it must NOT be scored as a clean bullish breakout.
    assert cq.false_breakout == "resistance" or cq.rejection_at == "resistance" or cq.quality_score <= 0


# --- Candle-closed confirmation ---------------------------------------------
def test_last_candle_not_closed_when_still_forming():
    now = datetime.now(timezone.utc)
    candles = [Candle(timestamp=now - timedelta(seconds=30), open=1, high=1.1, low=0.9, close=1)]
    assert not is_last_candle_closed(candles, timeframe_seconds=120, now=now)


def test_last_candle_closed_for_historical_data():
    candles = [Candle(timestamp=T0, open=1, high=1.1, low=0.9, close=1)]
    assert is_last_candle_closed(candles, timeframe_seconds=120, now=datetime.now(timezone.utc))


def test_validate_candles_rejects_unclosed_last_candle_when_required():
    now = datetime.now(timezone.utc)
    candles = [
        Candle(timestamp=now - timedelta(seconds=120 * (59 - i)), open=1, high=1.1, low=0.9, close=1)
        for i in range(59)
    ] + [Candle(timestamp=now - timedelta(seconds=10), open=1, high=1.1, low=0.9, close=1)]
    result = validate_candles(candles, 120, min_candles=50, require_last_candle_closed=True, now=now)
    assert not result.ok
    assert any(e.code == "LAST_CANDLE_NOT_CLOSED" for e in result.errors)


def test_validate_candles_allows_unclosed_last_candle_by_default():
    now = datetime.now(timezone.utc)
    candles = [
        Candle(timestamp=now - timedelta(seconds=120 * (59 - i)), open=1, high=1.1, low=0.9, close=1)
        for i in range(60)
    ]
    result = validate_candles(candles, 120, min_candles=50)  # require_last_candle_closed defaults False
    assert result.ok


# --- Market regime classification (engine-level) ---------------------------
def test_strong_trend_regime_detected(uptrend_candles):
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles)
    assert sig.market_regime in (
        MarketRegime.STRONG_TRENDING_BULLISH, MarketRegime.WEAK_TRENDING_BULLISH,
        MarketRegime.TRENDING_BULLISH,
    )


def test_signal_includes_new_cards(uptrend_candles):
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles)
    d = sig.to_dict()
    for field in ("adx", "stochastic", "williams_r", "candle_quality", "data_quality_score"):
        assert field in d
    assert 0 <= d["data_quality_score"] <= 100


def test_component_scores_cover_eight_categories(uptrend_candles):
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles)
    names = {c.name for c in sig.component_scores}
    assert names == {
        "trend", "momentum", "volatility", "price_action", "support_resistance",
        "candle_quality", "market_regime", "multi_timeframe",
    }
    total_weight = sum(c.weight for c in sig.component_scores)
    assert abs(total_weight - 1.0) < 1e-6


def test_data_quality_score_lower_without_confirmation(uptrend_candles):
    engine = SignalEngine()
    sig_no_confirm = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles)
    confirm = deterministic_series(60, start_price=uptrend_candles[0].open, drift=0.3, amplitude=0.4, step_seconds=180)
    sig_with_confirm = engine.generate(
        "TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles, confirmation_candles_by_label={"3m": confirm},
    )
    assert sig_with_confirm.data_quality_score >= sig_no_confirm.data_quality_score


# --- EV formula / confidence calibration (metrics) -------------------------
def _trade(outcome, profit, confidence=Confidence.MEDIUM, ts=None):
    from signals.models import MarketRegime as MR
    return BacktestTrade(
        index=0, timestamp=ts or datetime.now(timezone.utc), asset="A", timeframe="2m",
        direction=Direction.CALL, confidence=confidence, score=50.0,
        market_regime=MR.RANGING, entry_price=100.0, exit_price=101.0,
        outcome=outcome, profit=profit,
    )


def test_expected_value_matches_formula_for_known_inputs():
    # 10 trades, 80% win rate, payout r=0.8 -> EV = 0.8*0.8 - 0.2 = 0.44
    trades = [_trade("WIN", 0.8) for _ in range(8)] + [_trade("LOSS", -1.0) for _ in range(2)]
    ev = expected_value_per_stake(trades)
    assert ev is not None
    assert ev["win_probability"] == pytest.approx(0.8)
    assert ev["net_payout_multiple_r"] == pytest.approx(0.8)
    assert ev["expected_value_per_stake"] == pytest.approx(0.8 * 0.8 - 0.2, abs=1e-9)


def test_expected_value_none_without_both_outcomes():
    all_wins = [_trade("WIN", 0.8) for _ in range(5)]
    assert expected_value_per_stake(all_wins) is None


def test_confidence_calibration_detects_well_calibrated_ordering():
    trades = (
        [_trade("LOSS", -1.0, Confidence.LOW) for _ in range(8)]
        + [_trade("WIN", 0.8, Confidence.LOW) for _ in range(2)]
        + [_trade("WIN", 0.8, Confidence.HIGH) for _ in range(9)]
        + [_trade("LOSS", -1.0, Confidence.HIGH) for _ in range(1)]
    )
    result = confidence_calibration(trades)
    assert result["by_confidence_level"]["LOW"]["win_rate"] == pytest.approx(0.2)
    assert result["by_confidence_level"]["HIGH"]["win_rate"] == pytest.approx(0.9)
    assert result["well_calibrated"] is True


def test_confidence_calibration_null_with_small_samples():
    trades = [_trade("WIN", 0.8, Confidence.MEDIUM) for _ in range(3)]
    result = confidence_calibration(trades)
    assert result["well_calibrated"] is None
