"""Paper (simulated) trade record.

Nothing in this module places, modifies, or cancels any real order. A
``PaperTrade`` is purely a bookkeeping record of a hypothetical outcome
used to track a simulated account balance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from signals.models import Confidence, Direction


@dataclass
class PaperTrade:
    id: str
    timestamp: datetime
    asset: str
    timeframe: str
    signal_direction: Direction
    confidence: Confidence
    entry_price: float
    stake: float
    expiry_at: datetime
    reason_for_signal: str

    exit_price: Optional[float] = None
    status: str = "PENDING"  # PENDING | WIN | LOSS | SKIPPED_RISK_PAUSED
    profit: float = 0.0
    balance_after: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "asset": self.asset,
            "timeframe": self.timeframe,
            "signal": self.signal_direction.value,
            "confidence": self.confidence.value,
            "entry_price": self.entry_price,
            "stake": round(self.stake, 2),
            "expiry_at": self.expiry_at.isoformat(),
            "reason_for_signal": self.reason_for_signal,
            "exit_price": self.exit_price,
            "status": self.status,
            "profit": round(self.profit, 4),
            "balance_after": round(self.balance_after, 2) if self.balance_after is not None else None,
        }
