from datetime import datetime, timedelta, timezone

from data.models import Candle
from signals.engine import SignalEngine
from signals.models import Direction
from strategy.config import TIMEFRAME_CONFIGS


def test_strong_uptrend_produces_call(uptrend_candles):
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles)
    # with a clean steady uptrend, we expect enough aligned evidence for a call
    assert sig.direction.is_call


def test_strong_downtrend_produces_put(downtrend_candles):
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["15m"], downtrend_candles)
    assert sig.direction.is_put


def test_flat_market_prefers_no_trade(flat_candles):
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["3m"], flat_candles)
    assert sig.direction == Direction.NO_TRADE


def test_insufficient_data_returns_no_signal():
    engine = SignalEngine()
    tiny = [
        Candle(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=120 * i),
               open=1, high=1.1, low=0.9, close=1) for i in range(3)
    ]
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], tiny)
    assert sig.direction == Direction.NO_TRADE
    assert "INSUFFICIENT DATA" in sig.reasons_against[0]


def test_stale_data_returns_data_unavailable(uptrend_candles):
    engine = SignalEngine()
    # We can't pass `now` directly to engine.generate, so emulate staleness
    # via a very old candle series relative to the system's real "now".
    old_candles = [
        Candle(timestamp=datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=120 * i),
               open=c.open, high=c.high, low=c.low, close=c.close)
        for i, c in enumerate(uptrend_candles)
    ]
    sig = engine.generate(
        "TEST", TIMEFRAME_CONFIGS["2m"], old_candles, max_stale_seconds=3600,
    )
    assert sig.direction == Direction.NO_TRADE
    assert "DATA UNAVAILABLE" in sig.reasons_against[0]


def test_signal_never_fabricates_missing_confirmation_timeframe(uptrend_candles):
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles)
    assert sig.multi_timeframe["available"] is False
    assert any("not supplied" in note or "confirmation" in note.lower() for note in sig.data_quality_notes)


def test_signal_includes_all_required_fields(uptrend_candles):
    engine = SignalEngine()
    sig = engine.generate("ASSET/X", TIMEFRAME_CONFIGS["2m"], uptrend_candles)
    d = sig.to_dict()
    required = [
        "asset", "timeframe", "direction", "confidence", "score", "market_regime",
        "trend", "momentum", "volatility", "support_resistance", "macd", "rsi",
        "bollinger", "sar", "moving_averages", "price_action", "reasons_supporting",
        "reasons_against", "invalidation_condition", "suggested_expiry_window", "risk_warning",
    ]
    for field in required:
        assert field in d


def test_risk_warning_never_promises_guaranteed_outcome(uptrend_candles):
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles)
    forbidden = ["guaranteed win", "100% accurate", "can't lose", "guaranteed profit"]
    text = sig.risk_warning.lower()
    for phrase in forbidden:
        assert phrase not in text


def test_no_trade_preferred_on_conflicting_evidence():
    # Build a series that strongly conflicts: price above long-term MAs
    # (bullish trend) but with abrupt bearish momentum right at the end.
    from tests.conftest import deterministic_series
    base = deterministic_series(100, start_price=100, drift=0.3, amplitude=0.3)
    # sharp bearish candle at the end to create trend/momentum conflict
    last = base[-1]
    shock = type(last)(
        timestamp=last.timestamp + timedelta(seconds=120),
        open=last.close, high=last.close + 0.05,
        low=last.close - 5.0, close=last.close - 4.8,
    )
    candles = base + [shock]
    engine = SignalEngine()
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], candles)
    # Either NO TRADE, or if directional, score must still clear threshold;
    # the key invariant is it must never silently ignore the conflict.
    if sig.direction != Direction.NO_TRADE:
        trend_score = next(c for c in sig.component_scores if c.name == "trend").score
        momentum_score = next(c for c in sig.component_scores if c.name == "momentum").score
        assert not (abs(trend_score) >= 0.4 and abs(momentum_score) >= 0.4 and (trend_score > 0) != (momentum_score > 0))
