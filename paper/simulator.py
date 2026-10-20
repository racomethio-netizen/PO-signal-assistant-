"""Paper (demo) trading simulator.

PAPER mode is the only mode this project supports. Nothing here connects
to Pocket Option or any broker, places a real order, or can be switched
into a "live" mode. Every "trade" is a bookkeeping record of a
hypothetical outcome used to track a simulated account balance under the
configured risk limits. No martingale: stake is fixed by ``RiskManager``
and is never increased after a loss.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from backtest.engine import DEFAULT_PAYOUT_RATIO
from risk.manager import RiskManager
from signals.models import Direction, Signal

from .models import PaperTrade


class PaperAccount:
    def __init__(self, risk_manager: RiskManager, payout_ratio: float = DEFAULT_PAYOUT_RATIO) -> None:
        self.risk_manager = risk_manager
        self.payout_ratio = payout_ratio
        self.open_trades: Dict[str, PaperTrade] = {}
        self.history: List[PaperTrade] = []

    def open_trade(
        self,
        signal: Signal,
        entry_price: float,
        expiry_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[PaperTrade]:
        """Open a hypothetical paper trade from a generated Signal.

        Returns None if the signal itself is NO TRADE (nothing to simulate).
        If the risk manager reports PAUSED, a SKIPPED_RISK_PAUSED record is
        still logged for dashboard visibility, but it does not affect the
        balance or count as a trade.
        """
        now = now or datetime.now(timezone.utc)

        if signal.direction == Direction.NO_TRADE:
            return None

        trade_id = str(uuid.uuid4())
        reason = "; ".join(signal.reasons_supporting[:3]) or "See signal detail."

        can_trade, reason_blocked = self.risk_manager.can_trade()
        if not can_trade:
            trade = PaperTrade(
                id=trade_id,
                timestamp=now,
                asset=signal.asset,
                timeframe=signal.timeframe,
                signal_direction=signal.direction,
                confidence=signal.confidence,
                entry_price=entry_price,
                stake=0.0,
                expiry_at=now + timedelta(seconds=expiry_seconds),
                reason_for_signal=f"SKIPPED — {reason_blocked}",
                status="SKIPPED_RISK_PAUSED",
                balance_after=self.risk_manager.state.balance,
            )
            self.history.append(trade)
            return trade

        stake = self.risk_manager.compute_stake()
        trade = PaperTrade(
            id=trade_id,
            timestamp=now,
            asset=signal.asset,
            timeframe=signal.timeframe,
            signal_direction=signal.direction,
            confidence=signal.confidence,
            entry_price=entry_price,
            stake=stake,
            expiry_at=now + timedelta(seconds=expiry_seconds),
            reason_for_signal=reason,
            status="PENDING",
        )
        self.open_trades[trade_id] = trade
        self.history.append(trade)
        return trade

    def settle_trade(self, trade_id: str, exit_price: float) -> PaperTrade:
        trade = self.open_trades.pop(trade_id, None)
        if trade is None:
            raise KeyError(f"No open trade with id {trade_id}")

        win = (
            (trade.signal_direction.is_call and exit_price > trade.entry_price)
            or (trade.signal_direction.is_put and exit_price < trade.entry_price)
        )
        profit = trade.stake * self.payout_ratio if win else -trade.stake

        trade.exit_price = exit_price
        trade.status = "WIN" if win else "LOSS"
        trade.profit = profit

        self.risk_manager.record_trade_result(profit)
        trade.balance_after = self.risk_manager.state.balance
        return trade

    def expire_stale_pending(self, now: Optional[datetime] = None) -> None:
        """No-op placeholder: pending trades stay PENDING until the caller
        explicitly provides an exit price via settle_trade. This project
        never invents a market price to auto-settle a trade."""
        return None

    def reset(self) -> None:
        self.risk_manager.reset()
        self.open_trades.clear()
        self.history.clear()

    def equity_curve(self) -> List[float]:
        settled = [t for t in self.history if t.status in ("WIN", "LOSS")]
        curve = []
        balance = self.risk_manager.config.starting_balance
        for t in sorted(settled, key=lambda t: t.timestamp):
            balance += t.profit
            curve.append(balance)
        return curve

    def max_drawdown(self) -> float:
        curve = self.equity_curve()
        peak = self.risk_manager.config.starting_balance
        max_dd = 0.0
        for value in curve:
            peak = max(peak, value)
            max_dd = max(max_dd, peak - value)
        return max_dd

    def summary(self) -> Dict:
        settled = [t for t in self.history if t.status in ("WIN", "LOSS")]
        wins = [t for t in settled if t.status == "WIN"]
        losses = [t for t in settled if t.status == "LOSS"]
        risk_info = self.risk_manager.to_dict()
        return {
            "starting_balance": self.risk_manager.config.starting_balance,
            "balance": risk_info["balance"],
            "daily_pl": risk_info["daily_pl"],
            "daily_pl_pct": risk_info["daily_pl_pct"],
            "max_drawdown": round(self.max_drawdown(), 2),
            "total_trades": len(settled),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(settled)) if settled else None,
            "consecutive_losses": risk_info["consecutive_losses"],
            "current_stake": risk_info["current_stake"],
            "status": risk_info["status"],
            "status_reason": risk_info["reason"],
            "open_trades": len(self.open_trades),
            "pending_trade_ids": list(self.open_trades.keys()),
        }
