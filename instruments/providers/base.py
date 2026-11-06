"""The modular market-data provider interface.

This is the ONLY contract a market-data source has to satisfy to plug
into the existing signal engine — nothing downstream (validation,
indicators, scoring) changes based on which provider is configured. Every
method is read-only market data; none of them can place, modify, or
cancel an order, and none of them touch Pocket Option in any way.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from data.models import Candle

NO_REQUEST_YET = "No request made yet."


class ConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    STALE_DATA = "STALE DATA"
    AUTHENTICATION_ERROR = "AUTHENTICATION ERROR"
    RATE_LIMIT = "RATE LIMIT"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED INSTRUMENT"


@dataclass
class DataStatus:
    provider: str
    status: ConnectionStatus
    message: str
    last_successful_update: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "message": self.message,
            "last_successful_update": self.last_successful_update.isoformat()
            if self.last_successful_update else None,
        }


@dataclass
class ProviderInstrument:
    """One instrument as the PROVIDER itself describes it — this is what
    STEP 4 means by 'the actual instrument catalogue', not our own
    registry's opinion of what should exist."""
    symbol: str          # provider-native symbol, e.g. "EUR/USD"
    display_name: str
    category: str = "FOREX"


class ProviderError(RuntimeError):
    """Base class for provider failures. Never swallowed into fabricated
    data — every caller must handle this explicitly."""


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderUnsupportedInstrumentError(ProviderError):
    pass


class ProviderNetworkError(ProviderError):
    pass


class MarketDataProvider(ABC):
    name: str = "unknown"
    probe_symbol: str = ""  # a liquid instrument used only to test the connection

    def test_connection(self) -> DataStatus:
        """One cheap real request (a latest-price lookup) to learn whether
        the provider is reachable and the credentials work. The outcome is
        recorded in, and returned as, the provider's status — never raised."""
        try:
            self.get_latest_price(self.probe_symbol)
        except ProviderError:
            pass
        return self.get_data_status()

    @abstractmethod
    def get_available_instruments(self) -> List[ProviderInstrument]:
        """The provider's own tradable-instrument list. Must raise rather
        than return an invented list if the provider can't supply one."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_price(self, instrument: str) -> float:
        raise NotImplementedError

    @abstractmethod
    def get_candles(self, instrument: str, timeframe_seconds: int, limit: int) -> List[Candle]:
        """Return up to ``limit`` most recent CLOSED candles. Must raise
        ProviderUnsupportedInstrumentError rather than silently returning
        data for a different instrument, and must never return a
        still-forming candle as if it were closed."""
        raise NotImplementedError

    def subscribe_to_candles(self, instrument: str, timeframe_seconds: int):
        """Override only if the provider genuinely supports streaming.
        The default makes 'not supported' explicit rather than silently
        polling and pretending to stream."""
        raise NotImplementedError(f"{self.name} does not support streaming market data in this build.")

    @abstractmethod
    def get_data_status(self, instrument: Optional[str] = None) -> DataStatus:
        """Connection/health status for the dashboard (STEP 8). Reflects
        the outcome of the most recent real call to this provider, not a
        fresh network probe on every poll."""
        raise NotImplementedError
