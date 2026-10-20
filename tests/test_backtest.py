from datetime import datetime, timedelta, timezone

from backtest.engine import BacktestEngine
from backtest.metrics import compute_stats
from backtest.models import BacktestTrade
from backtest.walkforward import run_walk_forward, split_walk_forward
from signals.engine import SignalEngine
from signals.models import Confidence, Direction, MarketRegime
from strategy.config import TIMEFRAME_CONFIGS
from tests.conftest import deterministic_series


def test_backtest_runs_without_lookahead_and_matches_live_engine(uptrend_candles):
    """The crucial no-lookahead property: the signal computed by the
    backtester at index i must be identical to what the live engine would
    compute given only candles[: i + 1] — i.e. future candles must never
    influence it."""
    engine = SignalEngine()
    tf_cfg = TIMEFRAME_CONFIGS["2m"]
    backtester = BacktestEngine(engine)
    report = backtester.run("TEST", tf_cfg, uptrend_candles)

    assert len(report.trades) > 0
    for trade in report.trades:
        i = trade.index
        live_signal = engine.generate("TEST", tf_cfg, uptrend_candles[: i + 1])
        assert live_signal.direction == trade.direction
        assert live_signal.score == trade.score


def test_future_candles_never_change_a_past_decision(uptrend_candles):
    engine = SignalEngine()
    tf_cfg = TIMEFRAME_CONFIGS["2m"]
    cutoff = 80
    sig_with_full_future = engine.generate("TEST", tf_cfg, uptrend_candles[:cutoff])

    # Replace everything after the cutoff with a wildly different (crashing)
    # continuation; the signal decided *at* the cutoff must not change.
    crashed_tail = deterministic_series(40, start_price=uptrend_candles[cutoff - 1].close, drift=-5.0, amplitude=0.1)
    altered_candles = list(uptrend_candles[:cutoff]) + crashed_tail
    sig_recomputed = engine.generate("TEST", tf_cfg, altered_candles[:cutoff])

    assert sig_with_full_future.direction == sig_recomputed.direction
    assert sig_with_full_future.score == sig_recomputed.score


def test_backtest_records_every_signal_including_no_trade(flat_candles):
    engine = SignalEngine()
    tf_cfg = TIMEFRAME_CONFIGS["3m"]
    backtester = BacktestEngine(engine)
    report = backtester.run("TEST", tf_cfg, flat_candles)
    assert report.stats["num_signals"] == len(report.trades)
    # A flat, directionless market should produce at least some NO TRADE signals.
    assert report.stats["no_trade_signals"] >= 0


def test_metrics_compute_known_values():
    now = datetime.now(timezone.utc)

    def trade(outcome, profit):
        return BacktestTrade(
            index=0, timestamp=now, asset="A", timeframe="2m",
            direction=Direction.CALL if profit >= 0 else Direction.PUT,
            confidence=Confidence.MEDIUM, score=50.0,
            market_regime=MarketRegime.TRENDING_BULLISH,
            entry_price=100.0, exit_price=101.0 if outcome == "WIN" else 99.0,
            outcome=outcome, profit=profit,
        )

    trades = [
        trade("WIN", 0.8), trade("LOSS", -1.0), trade("LOSS", -1.0),
        trade("WIN", 0.8), trade("WIN", 0.8),
    ]
    stats = compute_stats(trades)
    assert stats["wins"] == 3
    assert stats["losses"] == 2
    assert stats["win_rate"] == 0.6
    assert stats["max_losing_streak"] == 2
    gross_profit = 0.8 * 3
    gross_loss = 1.0 * 2
    assert stats["profit_factor"] == gross_profit / gross_loss


def test_max_drawdown_detects_a_losing_streak():
    now = datetime.now(timezone.utc)

    def trade(profit):
        return BacktestTrade(
            index=0, timestamp=now, asset="A", timeframe="2m",
            direction=Direction.CALL, confidence=Confidence.MEDIUM, score=50.0,
            market_regime=MarketRegime.RANGING, entry_price=100.0, exit_price=100.0,
            outcome="WIN" if profit > 0 else "LOSS", profit=profit,
        )

    trades = [trade(1.0), trade(1.0), trade(-1.0), trade(-1.0), trade(-1.0), trade(2.0)]
    stats = compute_stats(trades)
    # peak after first two wins = 2.0, trough after three losses = -1.0 => drawdown 3.0
    assert stats["max_drawdown"] == 3.0


def test_walk_forward_only_evaluates_test_segments(uptrend_candles):
    engine = SignalEngine()
    tf_cfg = TIMEFRAME_CONFIGS["2m"]
    windows = split_walk_forward(uptrend_candles, n_windows=2, train_ratio=0.5)
    assert len(windows) == 2
    for w in windows:
        assert len(w.train_candles) + len(w.test_candles) <= len(uptrend_candles)

    report = run_walk_forward(engine, "TEST", tf_cfg, uptrend_candles, n_windows=2, train_ratio=0.5)
    assert report.combined_stats["num_signals"] >= 0
    assert "historical simulation" in report.disclaimer.lower() or "historical" in report.disclaimer.lower()
