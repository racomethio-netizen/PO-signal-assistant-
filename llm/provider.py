"""LLM provider interface.

Claude (or any other provider implementing this interface) is used ONLY
to turn already-computed deterministic analysis into human-readable
explanation text. It never calculates RSI/MACD/Bollinger/etc, never
decides the Direction/Confidence/Score, and can never override risk
controls or place a trade. If a provider call fails for any reason, the
caller must fall back to the fixed string below and continue with the
deterministic analysis unaffected.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

AI_UNAVAILABLE_MESSAGE = "AI EXPLANATION UNAVAILABLE"


class LLMProvider(ABC):
    @abstractmethod
    def explain_signal(self, signal) -> str:
        """Return a short human-readable explanation of an already-computed
        Signal. Must never raise — implementations should catch their own
        errors and return ``AI_UNAVAILABLE_MESSAGE`` instead."""
        raise NotImplementedError

    @abstractmethod
    def review_backtest(self, report_dict: Dict[str, Any]) -> str:
        """Return a short human-readable review of backtest results.
        Must never raise."""
        raise NotImplementedError
