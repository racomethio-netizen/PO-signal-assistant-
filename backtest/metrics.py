"""Aggregate statistics over a list of BacktestTrade records."""
from __future__ import annotations

from typing import Any, Dict, List

from .models import BacktestTrade


def _max_losing_streak(trades: List[BacktestTrade]) -> int:
    streak = 0
    worst = 0
    for t in trades:
        if t.outcome == "LOSS":
            streak += 1
            worst = max(worst, streak)
        elif t.outcome == "WIN":
            streak = 0
    return worst


def _max_drawdown(trades: List[BacktestTrade]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        if t.outcome is None:
            continue
        equity += t.profit
        peak = max(peak, equity)
        drawdown = peak - equity
        max_dd = max(max_dd, drawdown)
    return max_dd


def _profit_factor(trades: List[BacktestTrade]) -> float | None:
    gross_profit = sum(t.profit for t in trades if t.outcome == "WIN")
    gross_loss = abs(sum(t.profit for t in trades if t.outcome == "LOSS"))
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def compute_stats(trades: List[BacktestTrade]) -> Dict[str, Any]:
    decided = [t for t in trades if t.outcome is not None]
    wins = [t for t in decided if t.outcome == "WIN"]
    losses = [t for t in decided if t.outcome == "LOSS"]

    call_signals = [t for t in trades if t.direction.is_call]
    put_signals = [t for t in trades if t.direction.is_put]
    no_trade_signals = [t for t in trades if t.outcome is None]

    win_rate = len(wins) / len(decided) if decided else None
    average_outcome = sum(t.profit for t in decided) / len(decided) if decided else None

    return {
        "num_signals": len(trades),
        "call_signals": len(call_signals),
        "put_signals": len(put_signals),
        "no_trade_signals": len(no_trade_signals),
        "decided_trades": len(decided),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "average_outcome": average_outcome,
        "max_losing_streak": _max_losing_streak(decided),
        "max_drawdown": _max_drawdown(decided),
        "profit_factor": _profit_factor(decided),
        "by_timeframe": _group_by(trades, lambda t: t.timeframe),
        "by_asset": _group_by(trades, lambda t: t.asset),
        "by_market_regime": _group_by(trades, lambda t: t.market_regime.value),
        "by_confidence": _group_by(trades, lambda t: t.confidence.value),
    }


def _group_by(trades: List[BacktestTrade], key_fn) -> Dict[str, Any]:
    groups: Dict[str, List[BacktestTrade]] = {}
    for t in trades:
        groups.setdefault(key_fn(t), []).append(t)

    out: Dict[str, Any] = {}
    for key, group in groups.items():
        decided = [t for t in group if t.outcome is not None]
        wins = [t for t in decided if t.outcome == "WIN"]
        out[key] = {
            "num_signals": len(group),
            "decided_trades": len(decided),
            "wins": len(wins),
            "win_rate": (len(wins) / len(decided)) if decided else None,
            "average_outcome": (sum(t.profit for t in decided) / len(decided)) if decided else None,
        }
    return out
