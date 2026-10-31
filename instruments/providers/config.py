"""Environment-based market-data provider configuration (STEP 3).

MARKET_DATA_PROVIDER / MARKET_DATA_API_KEY / MARKET_DATA_BASE_URL select
and configure exactly one provider for the whole app. Nothing here
prints, logs, or returns the API key — ``get_configured_provider`` is the
only place it's read, and it's held only in the provider instance's
memory for the life of the process.
"""
from __future__ import annotations

import os
from typing import Optional

from .base import MarketDataProvider
from .binance_provider import BinanceProvider
from .twelvedata import TwelveDataProvider

SUPPORTED_PROVIDERS = ("twelvedata", "binance")


class ProviderConfigError(ValueError):
    pass


def get_configured_provider(env: Optional[dict] = None) -> Optional[MarketDataProvider]:
    """Build the provider named by MARKET_DATA_PROVIDER, or None if unset.

    Raises ProviderConfigError (never a bare crash) for a recognized-but-
    misconfigured provider (e.g. twelvedata without an API key) or an
    unrecognized provider name — callers decide how to surface that.
    """
    env = env if env is not None else os.environ
    name = (env.get("MARKET_DATA_PROVIDER") or "").strip().lower()
    if not name:
        return None

    base_url = env.get("MARKET_DATA_BASE_URL") or None

    if name == "twelvedata":
        api_key = env.get("MARKET_DATA_API_KEY")
        if not api_key:
            raise ProviderConfigError(
                "MARKET_DATA_PROVIDER=twelvedata requires MARKET_DATA_API_KEY to be set."
            )
        return TwelveDataProvider(api_key=api_key, base_url=base_url)

    if name == "binance":
        # Binance's public endpoints need no API key; MARKET_DATA_BASE_URL
        # is accepted (e.g. to point at a self-hosted proxy) but optional.
        return BinanceProvider()

    raise ProviderConfigError(
        f"Unknown MARKET_DATA_PROVIDER '{name}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}."
    )
