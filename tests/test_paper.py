from datetime import datetime, timezone

from paper.simulator import PaperAccount
from risk.manager import RiskConfig, RiskManager
from signals.models import (
    Confidence, Direction, MarketRegime, Signal, no_trade_signal,
)


def make_signal(direction: Direction, confidence=Confidence.HIGH) -> Signal:
    now = datetime.now(timezone.utc)
    return Signal(
        asset="TEST/USD", timeframe="2m", generated_at=now,
        direction=direction, confidence=confidence, score=75.0,
        market_regime=MarketRegime.TRENDING_BULLISH, component_scores=[],
        trend={}, momentum={}, volatility={}, support_resistance={},
        macd={}, rsi={}, bollinger={}, sar={}, moving_averages={},
        price_action={}, multi_timeframe={},
        reasons_supporting=["strong trend"], reasons_against=[],
        invalidation_condition="n/a", suggested_expiry_window="~2 minutes",
    )


def make_account(**risk_overrides) -> PaperAccount:
    defaults = dict(starting_balance=1000.0, max_stake_pct=0.01, max_consecutive_losses=3, cooldown_trades=2)
    defaults.update(risk_overrides)
    rm = RiskManager(RiskConfig(**defaults))
    return PaperAccount(rm, payout_ratio=0.8)


def test_no_trade_signal_opens_nothing():
    acct = make_account()
    sig = no_trade_signal("TEST", "2m", datetime.now(timezone.utc), "NO TRADE")
    trade = acct.open_trade(sig, entry_price=100.0, expiry_seconds=120)
    assert trade is None


def test_open_and_settle_winning_trade_increases_balance():
    acct = make_account()
    sig = make_signal(Direction.CALL)
    trade = acct.open_trade(sig, entry_price=100.0, expiry_seconds=120)
    assert trade.status == "PENDING"
    settled = acct.settle_trade(trade.id, exit_price=101.0)  # price rose -> CALL wins
    assert settled.status == "WIN"
    assert settled.profit > 0
    assert acct.risk_manager.state.balance == 1000.0 + settled.profit


def test_open_and_settle_losing_trade_decreases_balance():
    acct = make_account()
    sig = make_signal(Direction.CALL)
    trade = acct.open_trade(sig, entry_price=100.0, expiry_seconds=120)
    settled = acct.settle_trade(trade.id, exit_price=99.0)  # price fell -> CALL loses
    assert settled.status == "LOSS"
    assert settled.profit < 0
    assert acct.risk_manager.state.balance == 1000.0 + settled.profit


def test_stake_amount_never_increases_after_consecutive_losses():
    acct = make_account(max_consecutive_losses=10)
    stakes = []
    for _ in range(4):
        sig = make_signal(Direction.PUT)
        trade = acct.open_trade(sig, entry_price=100.0, expiry_seconds=120)
        stakes.append(trade.stake)
        acct.settle_trade(trade.id, exit_price=101.0)  # PUT loses every time
    for i in range(1, len(stakes)):
        assert stakes[i] <= stakes[i - 1]


def test_trade_skipped_when_risk_paused():
    acct = make_account(max_consecutive_losses=1, cooldown_trades=5)
    sig = make_signal(Direction.PUT)
    trade1 = acct.open_trade(sig, entry_price=100.0, expiry_seconds=120)
    acct.settle_trade(trade1.id, exit_price=101.0)  # one loss hits the limit -> PAUSED

    sig2 = make_signal(Direction.PUT)
    trade2 = acct.open_trade(sig2, entry_price=100.0, expiry_seconds=120)
    assert trade2.status == "SKIPPED_RISK_PAUSED"
    assert trade2.stake == 0.0


def test_reset_clears_history_and_restores_balance():
    acct = make_account()
    sig = make_signal(Direction.CALL)
    trade = acct.open_trade(sig, entry_price=100.0, expiry_seconds=120)
    acct.settle_trade(trade.id, exit_price=101.0)
    acct.reset()
    assert acct.history == []
    assert acct.open_trades == {}
    assert acct.risk_manager.state.balance == 1000.0


def test_summary_reports_consistent_counts():
    acct = make_account()
    for exit_price, direction in [(101.0, Direction.CALL), (99.0, Direction.CALL)]:
        sig = make_signal(direction)
        trade = acct.open_trade(sig, entry_price=100.0, expiry_seconds=120)
        acct.settle_trade(trade.id, exit_price=exit_price)
    summary = acct.summary()
    assert summary["total_trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["win_rate"] == 0.5


def test_settle_unknown_trade_id_raises_keyerror():
    acct = make_account()
    try:
        acct.settle_trade("does-not-exist", exit_price=1.0)
        assert False, "expected KeyError"
    except KeyError:
        pass
