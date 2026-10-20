"""Signal output model.

Every signal produced by this system is a probabilistic, explainable
technical-analysis opinion — never a guarantee. See ``Signal.risk_warning``
which is populated on every signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Direction(str, Enum):
    STRONG_CALL = "STRONG CALL"
    CALL = "CALL"
    WEAK_CALL = "WEAK CALL"
    NO_TRADE = "NO TRADE"
    WEAK_PUT = "WEAK PUT"
    PUT = "PUT"
    STRONG_PUT = "STRONG PUT"

    @property
    def is_call(self) -> bool:
        return self in (Direction.STRONG_CALL, Direction.CALL, Direction.WEAK_CALL)

    @property
    def is_put(self) -> bool:
        return self in (Direction.STRONG_PUT, Direction.PUT, Direction.WEAK_PUT)


class Confidence(str, Enum):
    VERY_LOW = "VERY LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY HIGH"


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    VOLATILE_CHOPPY = "VOLATILE_CHOPPY"
    UNKNOWN = "UNKNOWN"


RISK_WARNING = (
    "This is a probabilistic technical-analysis opinion based on historical "
    "price data, not a guaranteed outcome. No indicator combination, and no "
    "confidence level, guarantees a winning trade. Past technical alignment "
    "does not predict future results. Trade only in PAPER/DEMO mode unless "
    "you fully understand and accept the risk of loss."
)


@dataclass
class ComponentScore:
    """One of the seven independent evidence scores."""

    name: str
    score: float  # signed, -1.0 (bearish) .. +1.0 (bullish)
    weight: float
    weighted_contribution: float
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "weight": self.weight,
            "weighted_contribution": round(self.weighted_contribution, 4),
            "detail": self.detail,
        }


@dataclass
class NoTradeFilterResult:
    triggered: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class Signal:
    asset: str
    timeframe: str
    generated_at: datetime

    direction: Direction
    confidence: Confidence
    score: float  # 0-100, magnitude of composite weighted evidence

    market_regime: MarketRegime

    component_scores: List[ComponentScore]

    trend: Dict[str, Any]
    momentum: Dict[str, Any]
    volatility: Dict[str, Any]
    support_resistance: Dict[str, Any]
    macd: Dict[str, Any]
    rsi: Dict[str, Any]
    bollinger: Dict[str, Any]
    sar: Dict[str, Any]
    moving_averages: Dict[str, Any]
    price_action: Dict[str, Any]
    multi_timeframe: Dict[str, Any]

    reasons_supporting: List[str]
    reasons_against: List[str]

    invalidation_condition: str
    suggested_expiry_window: str

    data_quality_notes: List[str] = field(default_factory=list)
    explanation: Optional[str] = None  # LLM-generated narrative, if available
    risk_warning: str = RISK_WARNING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset": self.asset,
            "timeframe": self.timeframe,
            "generated_at": self.generated_at.isoformat(),
            "direction": self.direction.value,
            "confidence": self.confidence.value,
            "score": round(self.score, 2),
            "market_regime": self.market_regime.value,
            "component_scores": [c.to_dict() for c in self.component_scores],
            "trend": self.trend,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "support_resistance": self.support_resistance,
            "macd": self.macd,
            "rsi": self.rsi,
            "bollinger": self.bollinger,
            "sar": self.sar,
            "moving_averages": self.moving_averages,
            "price_action": self.price_action,
            "multi_timeframe": self.multi_timeframe,
            "reasons_supporting": self.reasons_supporting,
            "reasons_against": self.reasons_against,
            "invalidation_condition": self.invalidation_condition,
            "suggested_expiry_window": self.suggested_expiry_window,
            "data_quality_notes": self.data_quality_notes,
            "explanation": self.explanation,
            "risk_warning": self.risk_warning,
        }


def no_trade_signal(
    asset: str,
    timeframe: str,
    generated_at: datetime,
    reason: str,
    detail_notes: Optional[List[str]] = None,
) -> Signal:
    """Build a minimal NO TRADE signal for cases where analysis can't even
    be attempted (e.g. DATA UNAVAILABLE, INSUFFICIENT DATA)."""
    return Signal(
        asset=asset,
        timeframe=timeframe,
        generated_at=generated_at,
        direction=Direction.NO_TRADE,
        confidence=Confidence.VERY_LOW,
        score=0.0,
        market_regime=MarketRegime.UNKNOWN,
        component_scores=[],
        trend={}, momentum={}, volatility={}, support_resistance={},
        macd={}, rsi={}, bollinger={}, sar={}, moving_averages={},
        price_action={}, multi_timeframe={},
        reasons_supporting=[],
        reasons_against=[reason],
        invalidation_condition="N/A — no signal was generated.",
        suggested_expiry_window="N/A",
        data_quality_notes=detail_notes or [reason],
        risk_warning=RISK_WARNING,
    )
