"""No-lookahead backtest engine.

At simulated time ``i``, the signal engine only ever sees
``candles[: i + 1]`` — the same slice a live system would have had at that
moment. The outcome of a hypothetical trade is read from a candle that is
strictly in the future of the decision point, exactly once, to score the
trade; it is never fed back into the signal calculation itself.

This runs the exact same ``SignalEngine`` used for live analysis, so
backtest results reflect the same scoring logic a live signal would use —
not a separate, idealized strategy.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from data.models import Candle
from signals.engine import SignalEngine
from signals.models import Direction
from strategy.config import TimeframeConfig

from .metrics import compute_stats
from .models import BacktestReport, BacktestTrade

DEFAULT_PAYOUT_RATIO = 0.8  # typical binary-option style payout on a win
DEFAULT_STAKE_UNITS = 1.0


class BacktestEngine:
    def __init__(self, signal_engine: SignalEngine) -> None:
        self.signal_engine = signal_engine

    def run(
        self,
        asset: str,
        timeframe_config: TimeframeConfig,
        candles: Sequence[Candle],
        confirmation_full_history: Optional[Dict[str, Sequence[Candle]]] = None,
        payout_ratio: float = DEFAULT_PAYOUT_RATIO,
        stake_units: float = DEFAULT_STAKE_UNITS,
        min_score_threshold: Optional[float] = None,
    ) -> BacktestReport:
        confirmation_full_history = confirmation_full_history or {}

        if min_score_threshold is not None:
            original_threshold = self.signal_engine.min_score_threshold
            self.signal_engine.min_score_threshold = min_score_threshold
        else:
            original_threshold = None

        try:
            trades = self._simulate(
                asset, timeframe_config, candles, confirmation_full_history,
                payout_ratio, stake_units,
            )
        finally:
            if original_threshold is not None:
                self.signal_engine.min_score_threshold = original_threshold

        report = BacktestReport(asset=asset, timeframe=timeframe_config.name, trades=trades)
        report.stats = compute_stats(trades)
        return report

    def _simulate(
        self,
        asset: str,
        timeframe_config: TimeframeConfig,
        candles: Sequence[Candle],
        confirmation_full_history: Dict[str, Sequence[Candle]],
        payout_ratio: float,
        stake_units: float,
    ) -> List[BacktestTrade]:
        n = len(candles)
        expiry_bars = timeframe_config.expiry_bars
        min_i = timeframe_config.min_candles - 1
        max_i = n - expiry_bars - 1

        trades: List[BacktestTrade] = []
        i = min_i
        while i <= max_i:
            history_slice = candles[: i + 1]
            cutoff_ts = history_slice[-1].timestamp

            confirmation_slice = {
                label: [c for c in full if c.timestamp <= cutoff_ts]
                for label, full in confirmation_full_history.items()
            }

            signal = self.signal_engine.generate(
                asset=asset,
                timeframe_config=timeframe_config,
                candles=history_slice,
                confirmation_candles_by_label=confirmation_slice,
            )

            entry_price = candles[i].close
            if signal.direction == Direction.NO_TRADE:
                trades.append(
                    BacktestTrade(
                        index=i,
                        timestamp=candles[i].timestamp,
                        asset=asset,
                        timeframe=timeframe_config.name,
                        direction=signal.direction,
                        confidence=signal.confidence,
                        score=signal.score,
                        market_regime=signal.market_regime,
                        entry_price=entry_price,
                        exit_price=None,
                        outcome=None,
                        profit=0.0,
                    )
                )
                i += 1
                continue

            expiry_index = i + expiry_bars
            exit_price = candles[expiry_index].close
            win = (
                (signal.direction.is_call and exit_price > entry_price)
                or (signal.direction.is_put and exit_price < entry_price)
            )
            profit = stake_units * payout_ratio if win else -stake_units

            trades.append(
                BacktestTrade(
                    index=i,
                    timestamp=candles[i].timestamp,
                    asset=asset,
                    timeframe=timeframe_config.name,
                    direction=signal.direction,
                    confidence=signal.confidence,
                    score=signal.score,
                    market_regime=signal.market_regime,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    outcome="WIN" if win else "LOSS",
                    profit=profit,
                )
            )
            # A trade occupies the asset until its expiry resolves; the next
            # decision point is after that, matching realistic trade cadence.
            i += expiry_bars

        return trades
