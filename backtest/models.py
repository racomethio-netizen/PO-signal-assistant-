"""Backtest record types.

IMPORTANT: every result produced by this module is a historical
simulation over past data. It is NOT evidence of future profitability,
and must always be presented to the user with that caveat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from signals.models import Confidence, Direction, MarketRegime


@dataclass
class BacktestTrade:
    index: int
    timestamp: datetime
    asset: str
    timeframe: str
    direction: Direction
    confidence: Confidence
    score: float
    market_regime: MarketRegime
    entry_price: float
    exit_price: Optional[float]
    outcome: Optional[str]  # "WIN" | "LOSS" | None (NO TRADE)
    profit: float  # in stake units; 0 for NO TRADE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "timestamp": self.timestamp.isoformat(),
            "asset": self.asset,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "confidence": self.confidence.value,
            "score": round(self.score, 2),
            "market_regime": self.market_regime.value,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "outcome": self.outcome,
            "profit": round(self.profit, 4),
        }


@dataclass
class BacktestReport:
    asset: str
    timeframe: str
    trades: List[BacktestTrade] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    disclaimer: str = (
        "These results are a HISTORICAL SIMULATION over past data only. "
        "They are not evidence of future profitability. Market conditions, "
        "slippage, payout rates, and execution timing in live trading can "
        "differ materially from this simulation."
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset": self.asset,
            "timeframe": self.timeframe,
            "trades": [t.to_dict() for t in self.trades],
            "stats": self.stats,
            "disclaimer": self.disclaimer,
        }
