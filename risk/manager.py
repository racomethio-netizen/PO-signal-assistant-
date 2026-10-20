"""Risk management for paper trading.

Hard rules enforced here, with no override path anywhere in this
codebase:
  * Stake is always a fraction of the CURRENT balance (or a fixed amount
    capped at that fraction) — it never increases after a loss. There is
    no martingale logic anywhere in this project.
  * When a configured limit is breached, status becomes PAUSED and no
    further hypothetical trades are allowed until a new day, a cooldown
    elapses, or the user manually resets the session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class RiskStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CAUTION = "CAUTION"
    PAUSED = "TRADING PAUSED"


@dataclass(frozen=True)
class RiskConfig:
    starting_balance: float = 1000.0
    stake_mode: str = "percent"  # "percent" | "fixed"
    fixed_stake_amount: float = 10.0
    max_stake_pct: float = 0.01
    max_daily_loss_pct: float = 0.03
    max_consecutive_losses: int = 3
    cooldown_trades: int = 5
    max_trades_per_day: int = 20

    def __post_init__(self) -> None:
        if self.stake_mode not in ("percent", "fixed"):
            raise ValueError("stake_mode must be 'percent' or 'fixed'")
        if not (0 < self.max_stake_pct <= 0.2):
            raise ValueError("max_stake_pct must be in (0, 0.2] as a sanity bound")


@dataclass
class RiskState:
    balance: float
    daily_start_balance: float
    daily_pl: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    cooldown_remaining: int = 0
    current_day: date = field(default_factory=date.today)


class RiskManager:
    def __init__(self, config: RiskConfig = RiskConfig()) -> None:
        self.config = config
        self.state = RiskState(
            balance=config.starting_balance,
            daily_start_balance=config.starting_balance,
        )

    def _roll_day_if_needed(self, today: Optional[date] = None) -> None:
        today = today or date.today()
        if today != self.state.current_day:
            self.state.current_day = today
            self.state.daily_start_balance = self.state.balance
            self.state.daily_pl = 0.0
            self.state.trades_today = 0

    def compute_stake(self) -> float:
        """Current hypothetical stake size. Always derived from the
        CURRENT balance and the configured cap — never raised after a loss."""
        cap = self.state.balance * self.config.max_stake_pct
        if self.config.stake_mode == "fixed":
            return min(self.config.fixed_stake_amount, cap)
        return cap

    def status(self) -> tuple[RiskStatus, str]:
        self._roll_day_if_needed()
        cfg = self.config
        st = self.state

        daily_loss_limit = cfg.max_daily_loss_pct * st.daily_start_balance
        if daily_loss_limit > 0 and -st.daily_pl >= daily_loss_limit:
            return RiskStatus.PAUSED, (
                f"Daily loss limit reached: {-st.daily_pl:.2f} >= "
                f"{daily_loss_limit:.2f} ({cfg.max_daily_loss_pct*100:.1f}% of day's "
                "starting balance)."
            )

        if st.consecutive_losses >= cfg.max_consecutive_losses and st.cooldown_remaining > 0:
            return RiskStatus.PAUSED, (
                f"{st.consecutive_losses} consecutive losses reached the configured "
                f"limit of {cfg.max_consecutive_losses}; cooling down for "
                f"{st.cooldown_remaining} more trade slot(s)."
            )

        if st.trades_today >= cfg.max_trades_per_day:
            return RiskStatus.PAUSED, (
                f"Maximum trades per day reached ({cfg.max_trades_per_day})."
            )

        if -st.daily_pl >= 0.5 * daily_loss_limit and daily_loss_limit > 0:
            return RiskStatus.CAUTION, "Approaching daily loss limit."
        if st.consecutive_losses == max(cfg.max_consecutive_losses - 1, 0):
            return RiskStatus.CAUTION, "One more loss will trigger a cooldown pause."
        if st.trades_today >= int(0.8 * cfg.max_trades_per_day):
            return RiskStatus.CAUTION, "Approaching the daily trade count limit."

        return RiskStatus.ACTIVE, "Within all configured risk limits."

    def can_trade(self) -> tuple[bool, str]:
        status, reason = self.status()
        return status != RiskStatus.PAUSED, reason

    def record_trade_result(self, profit: float) -> None:
        """Record the outcome of one hypothetical paper trade.

        ``profit`` is positive for a win, negative for a loss. Stake size
        for the NEXT trade is always recomputed from the resulting
        balance via ``compute_stake()`` — it is never increased as a
        reaction to this loss.
        """
        self._roll_day_if_needed()
        self.state.balance += profit
        self.state.daily_pl += profit
        self.state.trades_today += 1

        just_hit_limit = False
        if profit < 0:
            self.state.consecutive_losses += 1
            if (
                self.state.consecutive_losses >= self.config.max_consecutive_losses
                and self.state.cooldown_remaining == 0
            ):
                self.state.cooldown_remaining = self.config.cooldown_trades
                just_hit_limit = True
        else:
            self.state.consecutive_losses = 0

        if not just_hit_limit:
            self.tick_cooldown()

    def tick_cooldown(self) -> None:
        """Advance the cooldown counter by one trade slot (call this once
        per evaluated signal, win/loss/no-trade, while in cooldown)."""
        if self.state.cooldown_remaining > 0:
            self.state.cooldown_remaining -= 1

    def reset(self) -> None:
        """Manual reset of the entire paper-trading risk session."""
        self.state = RiskState(
            balance=self.config.starting_balance,
            daily_start_balance=self.config.starting_balance,
        )

    def to_dict(self) -> dict:
        status, reason = self.status()
        return {
            "status": status.value,
            "reason": reason,
            "balance": round(self.state.balance, 2),
            "daily_pl": round(self.state.daily_pl, 2),
            "daily_pl_pct": (
                round(self.state.daily_pl / self.state.daily_start_balance * 100, 2)
                if self.state.daily_start_balance else 0.0
            ),
            "trades_today": self.state.trades_today,
            "consecutive_losses": self.state.consecutive_losses,
            "cooldown_remaining": self.state.cooldown_remaining,
            "current_stake": round(self.compute_stake(), 2),
            "max_trades_per_day": self.config.max_trades_per_day,
        }
