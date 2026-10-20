"""Aggregate statistics over a list of BacktestTrade records."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from signals.models import Confidence

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


def _wilson_interval(wins: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    """95% Wilson score confidence interval for a win-rate proportion.
    More reliable than a normal approximation for the small sample sizes
    typical of a short-timeframe strategy backtest."""
    if n == 0:
        return None
    p = wins / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = (z * math.sqrt((p * (1 - p) + z**2 / (4 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def expected_value_per_stake(trades: List[BacktestTrade]) -> Optional[Dict[str, Any]]:
    """EV per unit stake: EV = win_probability * r - (1 - win_probability),
    where r is the NET payout multiple on a win (profit only, stake
    excluded — e.g. r=0.8 means a $1 stake nets $0.80 profit on a win).
    r is derived empirically from this backtest's own winning trades
    rather than assumed, so it reflects whatever payout_ratio was
    actually configured for the run.
    """
    decided = [t for t in trades if t.outcome is not None]
    if not decided:
        return None
    wins = [t for t in decided if t.outcome == "WIN"]
    losses = [t for t in decided if t.outcome == "LOSS"]
    if not wins or not losses:
        return None

    avg_loss_stake = abs(sum(t.profit for t in losses) / len(losses))
    if avg_loss_stake == 0:
        return None
    r = (sum(t.profit for t in wins) / len(wins)) / avg_loss_stake

    win_probability = len(wins) / len(decided)
    ev = win_probability * r - (1 - win_probability)
    return {
        "win_probability": win_probability,
        "net_payout_multiple_r": r,
        "expected_value_per_stake": ev,
        "sample_size": len(decided),
        "formula": "EV = win_probability * r - (1 - win_probability)",
    }


CONFIDENCE_ORDER = [c.value for c in Confidence]


def confidence_calibration(trades: List[BacktestTrade]) -> Dict[str, Any]:
    """Does win rate actually increase with the confidence label, as it
    should if confidence is well-calibrated? Reports win rate + a 95%
    Wilson interval + sample size per level, in order, plus a boolean flag
    (only meaningful where sample sizes are large enough to compare)."""
    decided = [t for t in trades if t.outcome is not None]
    by_level: Dict[str, Dict[str, Any]] = {}
    for level in CONFIDENCE_ORDER:
        group = [t for t in decided if t.confidence.value == level]
        wins = [t for t in group if t.outcome == "WIN"]
        interval = _wilson_interval(len(wins), len(group)) if group else None
        by_level[level] = {
            "sample_size": len(group),
            "win_rate": (len(wins) / len(group)) if group else None,
            "win_rate_95pct_ci": interval,
        }

    MIN_SAMPLE_FOR_CALIBRATION_CHECK = 10
    observed = [
        (level, by_level[level]["win_rate"])
        for level in CONFIDENCE_ORDER
        if by_level[level]["sample_size"] >= MIN_SAMPLE_FOR_CALIBRATION_CHECK
    ]
    well_calibrated: Optional[bool] = None
    if len(observed) >= 2:
        rates = [r for _, r in observed]
        well_calibrated = all(rates[i] <= rates[i + 1] + 1e-9 for i in range(len(rates) - 1))

    return {
        "by_confidence_level": by_level,
        "well_calibrated": well_calibrated,
        "note": (
            "well_calibrated is null unless at least two confidence levels have "
            f">= {MIN_SAMPLE_FOR_CALIBRATION_CHECK} decided trades each; with small "
            "samples this is not a reliable judgment either way."
        ),
    }


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
        "expected_value": expected_value_per_stake(trades),
        "confidence_calibration": confidence_calibration(trades),
        "by_timeframe": _group_by(trades, lambda t: t.timeframe),
        "by_asset": _group_by(trades, lambda t: t.asset),
        "by_market_regime": _group_by(trades, lambda t: t.market_regime.value),
        "by_confidence": _group_by(trades, lambda t: t.confidence.value),
        "by_hour_of_day_utc": _group_by(trades, lambda t: f"{t.timestamp.hour:02d}:00"),
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
