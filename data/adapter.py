"""Interface for a future authorized market-data adapter.

This module intentionally contains NO implementation that connects to
Pocket Option or any other broker. It exists only as a typed extension
point so that, in the future, a properly authorized market-data source
(e.g. a licensed data vendor's public API) can be wired in without
touching the indicator/strategy/signal code.

Rules for any future implementation of this interface:
  * It may only fetch read-only market data (OHLCV candles).
  * It must never accept, store, or transmit account credentials, 2FA
    codes, session cookies, or auth tokens for Pocket Option or any broker.
  * It must never place, modify, or cancel trades.
  * It must never bypass CAPTCHAs, rate limits, or other platform
    protections.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .models import Candle


class MarketDataAdapter(ABC):
    """Abstract read-only OHLCV data source."""

    @abstractmethod
    def fetch_candles(self, asset: str, timeframe_seconds: int, limit: int) -> List[Candle]:
        """Return up to ``limit`` most recent closed candles for ``asset``.

        Implementations must raise rather than return synthetic/fabricated
        data when the underlying source cannot supply real candles.
        """
        raise NotImplementedError
