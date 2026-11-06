"""The configurable instrument registry.

Ships with a default catalog (major/cross forex pairs, their OTC
variants, and a handful of common crypto pairs), but nothing here is
hardcoded as exhaustive or authoritative: ``register()`` lets a caller
add any instrument not in the defaults (a user-provided list, or one
discovered from a configured data source), and availability is always
computed from what data has actually been loaded — never assumed from
being merely present in the catalog.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .models import AvailabilityStatus, DataSource, Instrument, InstrumentCategory, InstrumentType

# Pairs with no live/authorized data source wired up today default to
# "manual" — i.e. the user must supply candles themselves. Only crypto via
# Binance's public API (data/binance_adapter.py) is a verified live source.
_FOREX_MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]
_FOREX_CROSSES = [
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD",
    "CADJPY", "CHFJPY",
]
_CRYPTO_DEFAULTS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

# Pocket Option's OTC roster varies and isn't published as a stable API;
# we pre-register OTC variants only for the majors (the ones consistently
# offered as OTC in practice) and leave the rest to explicit registration
# via register() once a user confirms what their account actually shows.
_OTC_CANDIDATES = _FOREX_MAJORS


def _split_pair(symbol: str) -> tuple[str, str]:
    return symbol[:3], symbol[3:6]


def _forex_instrument(symbol: str, otc: bool) -> Instrument:
    base, quote = _split_pair(symbol)
    display = f"{base}/{quote}" + (" (OTC)" if otc else "")
    return Instrument(
        symbol=f"{symbol}_OTC" if otc else symbol,
        display_name=display,
        base_currency=base,
        quote_currency=quote,
        category=InstrumentCategory.FOREX,
        instrument_type=InstrumentType.OTC if otc else InstrumentType.STANDARD,
        # No authorized live forex feed is wired up in this build — see
        # README. Marking it MANUAL (not fabricating a live source) is the
        # honest reflection of that, for both standard and OTC variants.
        data_source=DataSource.MANUAL,
    )


def guess_instrument(symbol: str, data_source: DataSource = DataSource.MANUAL) -> Instrument:
    """Best-effort generic entry for a symbol that wasn't pre-registered.

    Used so uploading data under an ad-hoc/custom symbol (the "user-
    provided list" path) keeps working exactly as before — it just also
    makes the instrument show up in the catalog from then on, rather than
    silently rejecting anything not in the default list.
    """
    base, quote = (symbol[:3], symbol[3:6]) if len(symbol) >= 6 else (symbol, "")
    return Instrument(
        symbol=symbol,
        display_name=symbol,
        base_currency=base,
        quote_currency=quote,
        category=InstrumentCategory.OTHER,
        instrument_type=InstrumentType.STANDARD,
        data_source=data_source,
    )


def crypto_instrument(symbol: str) -> Instrument:
    # Heuristic split good enough for the *USDT pairs we pre-register;
    # custom crypto registrations should pass base/quote explicitly.
    quote = "USDT" if symbol.endswith("USDT") else symbol[-3:]
    base = symbol[: -len(quote)]
    return Instrument(
        symbol=symbol,
        display_name=f"{base}/{quote}",
        base_currency=base,
        quote_currency=quote,
        category=InstrumentCategory.CRYPTO,
        instrument_type=InstrumentType.STANDARD,
        data_source=DataSource.BINANCE,
    )


_CRYPTO_BASES = ("BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "LTC", "DOT", "AVAX", "LINK", "TRX", "MATIC")


def looks_like_crypto(symbol: str) -> bool:
    """Used only to pick a category for a symbol that isn't in the catalog
    yet; a symbol that IS registered always keeps its registered category."""
    s = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    return s.endswith("USDT") or s.startswith(_CRYPTO_BASES)


def _default_catalog() -> Dict[str, Instrument]:
    catalog: Dict[str, Instrument] = {}
    for symbol in _FOREX_MAJORS + _FOREX_CROSSES:
        inst = _forex_instrument(symbol, otc=False)
        catalog[inst.symbol] = inst
    for symbol in _OTC_CANDIDATES:
        inst = _forex_instrument(symbol, otc=True)
        catalog[inst.symbol] = inst
    for symbol in _CRYPTO_DEFAULTS:
        inst = crypto_instrument(symbol)
        catalog[inst.symbol] = inst
    return catalog


STALE_AFTER_SECONDS = 3600  # candle data older than this reads as STALE, not AVAILABLE


class AssetRegistry:
    def __init__(self) -> None:
        self._instruments: Dict[str, Instrument] = _default_catalog()

    def register(self, instrument: Instrument) -> Instrument:
        """Add or replace an instrument — the extension point for a
        user-provided list or anything discovered from a configured data
        source. Never invents data; just declares what the symbol IS."""
        self._instruments[instrument.symbol] = instrument
        return instrument

    def get(self, symbol: str) -> Optional[Instrument]:
        return self._instruments.get(symbol)

    def is_known(self, symbol: str) -> bool:
        return symbol in self._instruments

    def list(
        self,
        category: Optional[InstrumentCategory] = None,
        instrument_type: Optional[InstrumentType] = None,
        query: Optional[str] = None,
    ) -> List[Instrument]:
        items = list(self._instruments.values())
        if category is not None:
            items = [i for i in items if i.category == category]
        if instrument_type is not None:
            items = [i for i in items if i.instrument_type == instrument_type]
        if query:
            q = query.strip().upper()
            items = [
                i for i in items
                if q in i.symbol.upper() or q in i.display_name.upper()
            ]
        return sorted(items, key=lambda i: i.symbol)

    def describe(
        self,
        symbol: str,
        candles_by_timeframe: Dict[str, list],
        now: Optional[datetime] = None,
    ) -> Optional[Dict]:
        """Instrument metadata + RUNTIME availability/freshness, computed
        fresh from whatever candles are actually loaded — never cached, so
        it can't silently go stale itself."""
        instrument = self.get(symbol)
        if instrument is None:
            return None
        now = now or datetime.now(timezone.utc)

        last_timestamp: Optional[datetime] = None
        for candles in candles_by_timeframe.values():
            if candles:
                ts = candles[-1].timestamp
                if last_timestamp is None or ts > last_timestamp:
                    last_timestamp = ts

        if instrument.data_source == DataSource.UNCONFIGURED and last_timestamp is None:
            status = AvailabilityStatus.UNSUPPORTED
        elif last_timestamp is None:
            status = AvailabilityStatus.CONFIGURED
        elif (now - last_timestamp) > timedelta(seconds=STALE_AFTER_SECONDS):
            status = AvailabilityStatus.STALE
        else:
            status = AvailabilityStatus.AVAILABLE

        out = instrument.to_dict()
        out["availability_status"] = status.value
        out["last_successful_data_timestamp"] = last_timestamp.isoformat() if last_timestamp else None
        out["timeframes_loaded"] = sorted(k for k, v in candles_by_timeframe.items() if v)
        return out
