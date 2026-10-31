"""Instrument/data-source consistency checks.

These guard against exactly the substitution the task calls out: never
assume an OTC instrument has the same price feed as its standard-market
counterpart, and never let a fetch from an authorized source (today:
Binance, crypto-only) be used for an instrument it can't actually supply.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import DataSource, Instrument, InstrumentCategory, InstrumentType
from .registry import AssetRegistry


@dataclass
class InstrumentCheck:
    ok: bool
    reason: str = ""


def check_instrument_known(registry: AssetRegistry, symbol: str) -> InstrumentCheck:
    if not registry.is_known(symbol):
        return InstrumentCheck(
            ok=False,
            reason=(
                f"'{symbol}' is not a registered instrument. Register it first "
                "(POST /api/instruments/register) with its base/quote currency, "
                "category, and OTC/standard designation."
            ),
        )
    return InstrumentCheck(ok=True)


def check_binance_fetch_allowed(instrument: Instrument) -> InstrumentCheck:
    """Binance is a verified crypto-only, standard-market source. It must
    never be used to fabricate forex or OTC data for a symbol that merely
    LOOKS similar."""
    if instrument.category != InstrumentCategory.CRYPTO:
        return InstrumentCheck(
            ok=False,
            reason=(
                f"'{instrument.symbol}' is a {instrument.category.value} instrument; "
                "Binance only supplies crypto pairs and cannot provide forex data."
            ),
        )
    if instrument.instrument_type == InstrumentType.OTC:
        return InstrumentCheck(
            ok=False,
            reason=(
                f"'{instrument.symbol}' is an OTC instrument; an exchange feed like "
                "Binance's standard-market data is never a valid substitute for an "
                "OTC price feed."
            ),
        )
    return InstrumentCheck(ok=True)


_PROVIDER_CATEGORIES = {
    "binance": InstrumentCategory.CRYPTO,
    "twelvedata": InstrumentCategory.FOREX,
}


def check_provider_fetch_allowed(instrument: Instrument, provider_name: str) -> InstrumentCheck:
    """Generalized version of check_binance_fetch_allowed for any
    configured provider (STEP 2/3's generic adapter). No provider wired
    up in this build supplies OTC data, so OTC is always rejected here —
    never silently served standard-market candles instead."""
    if instrument.instrument_type == InstrumentType.OTC:
        return InstrumentCheck(
            ok=False,
            reason=(
                f"'{instrument.symbol}' is an OTC instrument; no configured market-data "
                f"provider ({provider_name}) supplies verified OTC data, and standard-"
                "market data is never substituted for it."
            ),
        )
    expected_category = _PROVIDER_CATEGORIES.get(provider_name)
    if expected_category is not None and instrument.category != expected_category:
        return InstrumentCheck(
            ok=False,
            reason=(
                f"'{instrument.symbol}' is a {instrument.category.value} instrument; "
                f"the '{provider_name}' provider only supplies {expected_category.value} data."
            ),
        )
    return InstrumentCheck(ok=True)


def otc_data_unavailable_reason(instrument: Instrument) -> str:
    return (
        f"OTC DATA UNAVAILABLE — NO SIGNAL ('{instrument.display_name}' is an OTC "
        "instrument with no verified OTC data loaded; standard-market candles are "
        "never substituted for it.)"
    )
