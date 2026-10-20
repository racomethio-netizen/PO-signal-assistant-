"""Walk-forward evaluation.

Splits history into consecutive windows and evaluates each window's
"test" segment only, after a preceding "train" segment that is excluded
from scoring. This keeps parameter/threshold choices from being evaluated
on the same data they'd be tuned against, per the project's explicit
requirement to avoid optimizing against the evaluation dataset.

This module does NOT perform automatic parameter optimization — it
provides the train/test split structure so that any parameter choice is
reported on held-out data rather than the data it looked at.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from data.models import Candle
from signals.engine import SignalEngine
from strategy.config import TimeframeConfig

from .engine import BacktestEngine
from .metrics import compute_stats
from .models import BacktestReport, BacktestTrade


@dataclass
class WalkForwardWindow:
    train_candles: Sequence[Candle]
    test_candles: Sequence[Candle]


@dataclass
class WalkForwardReport:
    asset: str
    timeframe: str
    windows: List[BacktestReport] = field(default_factory=list)
    combined_stats: Dict = field(default_factory=dict)
    disclaimer: str = (
        "Walk-forward historical simulation: each window's results come "
        "only from data after its own train segment. This still reflects "
        "the past, not a guarantee of future performance."
    )

    def to_dict(self) -> Dict:
        return {
            "asset": self.asset,
            "timeframe": self.timeframe,
            "windows": [w.to_dict() for w in self.windows],
            "combined_stats": self.combined_stats,
            "disclaimer": self.disclaimer,
        }


def split_walk_forward(
    candles: Sequence[Candle],
    n_windows: int = 4,
    train_ratio: float = 0.5,
) -> List[WalkForwardWindow]:
    if n_windows < 1:
        raise ValueError("n_windows must be >= 1")
    window_size = len(candles) // n_windows
    windows: List[WalkForwardWindow] = []
    for w in range(n_windows):
        start = w * window_size
        end = len(candles) if w == n_windows - 1 else (w + 1) * window_size
        segment = candles[start:end]
        if len(segment) < 10:
            continue
        split_point = max(1, int(len(segment) * train_ratio))
        windows.append(
            WalkForwardWindow(
                train_candles=segment[:split_point],
                test_candles=segment[split_point:],
            )
        )
    return windows


def run_walk_forward(
    signal_engine: SignalEngine,
    asset: str,
    timeframe_config: TimeframeConfig,
    candles: Sequence[Candle],
    n_windows: int = 4,
    train_ratio: float = 0.5,
    confirmation_full_history: Optional[Dict[str, Sequence[Candle]]] = None,
) -> WalkForwardReport:
    backtester = BacktestEngine(signal_engine)
    windows = split_walk_forward(candles, n_windows, train_ratio)

    reports: List[BacktestReport] = []
    all_trades: List[BacktestTrade] = []
    for window in windows:
        if len(window.test_candles) < timeframe_config.min_candles + timeframe_config.expiry_bars + 1:
            continue
        report = backtester.run(
            asset, timeframe_config, window.test_candles,
            confirmation_full_history=confirmation_full_history,
        )
        reports.append(report)
        all_trades.extend(report.trades)

    return WalkForwardReport(
        asset=asset,
        timeframe=timeframe_config.name,
        windows=reports,
        combined_stats=compute_stats(all_trades),
    )
