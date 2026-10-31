"""The instrument (currency pair / asset) registry's data model.

This is metadata ABOUT an instrument — what it is, what data source feeds
it, whether it's standard or OTC — never market data itself. Price
history still lives entirely in ``AppState.candle_store`` (unchanged);
this module exists so the app can tell the difference between "EUR/USD"
and "EUR/USD (OTC)" instead of treating every asset as an opaque string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class InstrumentCategory(str, Enum):
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"
    OTHER = "OTHER"


class InstrumentType(str, Enum):
    STANDARD = "STANDARD"
    OTC = "OTC"


class DataSource(str, Enum):
    BINANCE = "binance"        # live fetch via data/binance_adapter.py (crypto only)
    TWELVEDATA = "twelvedata"  # live fetch via instruments/providers/twelvedata.py (forex, standard only)
    MANUAL = "manual"          # user-supplied CSV/JSON/manual entry only
    UNCONFIGURED = "unconfigured"  # registered, but no data source wired up yet


class AvailabilityStatus(str, Enum):
    """Computed at read time from AppState.candle_store — never stored
    statically, so it can't silently go stale. See AssetRegistry.describe()."""
    AVAILABLE = "AVAILABLE"       # has recent, validated candles loaded
    CONFIGURED = "CONFIGURED"     # known instrument, but no data loaded yet
    STALE = "STALE"               # has data, but it's old
    UNSUPPORTED = "UNSUPPORTED"   # no viable data source for this instrument at all


@dataclass(frozen=True)
class Instrument:
    symbol: str                    # canonical id, e.g. "EURUSD", "EURUSD_OTC", "BTCUSDT"
    display_name: str              # e.g. "EUR/USD", "EUR/USD (OTC)"
    base_currency: str
    quote_currency: str
    category: InstrumentCategory
    instrument_type: InstrumentType
    data_source: DataSource
    supported_timeframes: Tuple[str, ...] = ("2m", "3m", "15m")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "display_name": self.display_name,
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "category": self.category.value,
            "instrument_type": self.instrument_type.value,
            "data_source": self.data_source.value,
            "supported_timeframes": list(self.supported_timeframes),
        }
