from risk.manager import RiskConfig, RiskManager, RiskStatus


def make_manager(**overrides) -> RiskManager:
    defaults = dict(
        starting_balance=1000.0,
        max_stake_pct=0.01,
        max_daily_loss_pct=0.03,
        max_consecutive_losses=3,
        cooldown_trades=2,
        max_trades_per_day=20,
    )
    defaults.update(overrides)
    return RiskManager(RiskConfig(**defaults))


def test_initial_status_is_active():
    rm = make_manager()
    status, _ = rm.status()
    assert status == RiskStatus.ACTIVE


def test_stake_never_increases_after_a_loss():
    rm = make_manager()
    stake_before = rm.compute_stake()
    rm.record_trade_result(-stake_before)
    stake_after = rm.compute_stake()
    assert stake_after <= stake_before  # balance shrank, so stake shrinks or stays flat
    # Simulate several more losses and confirm it never goes UP after a loss.
    for _ in range(3):
        s = rm.compute_stake()
        rm.record_trade_result(-s)
        assert rm.compute_stake() <= s


def test_fixed_stake_mode_capped_by_max_pct():
    rm = make_manager(stake_mode="fixed", fixed_stake_amount=10_000.0, max_stake_pct=0.01)
    stake = rm.compute_stake()
    assert stake == rm.state.balance * 0.01  # fixed amount is way above the cap, so cap wins


def test_daily_loss_limit_triggers_paused():
    rm = make_manager(max_daily_loss_pct=0.03)
    loss = rm.config.starting_balance * 0.04  # exceeds 3% limit
    rm.record_trade_result(-loss)
    status, reason = rm.status()
    assert status == RiskStatus.PAUSED
    assert "Daily loss limit" in reason


def test_consecutive_losses_triggers_paused_with_cooldown():
    rm = make_manager(max_consecutive_losses=3, cooldown_trades=2)
    for _ in range(3):
        rm.record_trade_result(-1.0)
    status, reason = rm.status()
    assert status == RiskStatus.PAUSED
    assert "consecutive losses" in reason.lower()


def test_win_resets_consecutive_loss_streak():
    rm = make_manager(max_consecutive_losses=3)
    rm.record_trade_result(-1.0)
    rm.record_trade_result(-1.0)
    rm.record_trade_result(5.0)  # a win
    assert rm.state.consecutive_losses == 0
    status, _ = rm.status()
    assert status == RiskStatus.ACTIVE


def test_cooldown_eventually_clears_and_resumes_trading():
    rm = make_manager(max_consecutive_losses=2, cooldown_trades=2)
    rm.record_trade_result(-1.0)
    rm.record_trade_result(-1.0)  # hits the limit, cooldown_remaining = 2
    assert rm.status()[0] == RiskStatus.PAUSED
    # Cooldown ticks down on subsequent evaluated trades (even losing ones,
    # since consecutive_losses no longer re-triggers the limit while already paused).
    rm.record_trade_result(5.0)
    rm.record_trade_result(5.0)
    assert rm.state.cooldown_remaining == 0


def test_max_trades_per_day_triggers_paused():
    rm = make_manager(max_trades_per_day=2, max_consecutive_losses=100)
    rm.record_trade_result(1.0)
    rm.record_trade_result(1.0)
    status, reason = rm.status()
    assert status == RiskStatus.PAUSED
    assert "trades per day" in reason.lower()


def test_manual_reset_restores_active_and_starting_balance():
    rm = make_manager()
    rm.record_trade_result(-500.0)
    rm.reset()
    status, _ = rm.status()
    assert status == RiskStatus.ACTIVE
    assert rm.state.balance == rm.config.starting_balance
    assert rm.state.consecutive_losses == 0


def test_no_martingale_config_rejects_absurd_stake_pct():
    try:
        RiskConfig(max_stake_pct=5.0)  # 500% of balance is not a sane cap
        assert False, "expected ValueError"
    except ValueError:
        pass
