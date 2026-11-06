"""Binance as a MarketDataProvider.

This WRAPS the existing, already-tested ``data/binance_adapter.py`` —
``get_candles`` delegates straight to it, unchanged. The only things
added here are the two extra documented Binance REST endpoints the
richer provider interface needs (instrument catalogue, latest price) and
status tracking; no existing Binance code is modified.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from data.binance_adapter import BINANCE_BASE_URL, BinanceAdapter, BinanceDataFormatError, normalize_symbol
from data.models import Candle

from .base import (
    NO_REQUEST_YET,
    ConnectionStatus,
    DataStatus,
    MarketDataProvider,
    ProviderInstrument,
    ProviderNetworkError,
    ProviderUnsupportedInstrumentError,
)

EXCHANGE_INFO_ENDPOINT = "/api/v3/exchangeInfo"  # real, documented Binance endpoint
TICKER_PRICE_ENDPOINT = "/api/v3/ticker/price"   # real, documented Binance endpoint


class BinanceProvider(MarketDataProvider):
    name = "binance"
    probe_symbol = "BTCUSDT"

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self._adapter = BinanceAdapter(timeout_seconds=timeout_seconds)
        self._timeout = timeout_seconds
        self._last_status = DataStatus(
            provider=self.name, status=ConnectionStatus.DISCONNECTED, message=NO_REQUEST_YET,
        )

    def _record_success(self) -> None:
        self._last_status = DataStatus(
            provider=self.name, status=ConnectionStatus.CONNECTED,
            message="Last request succeeded.", last_successful_update=datetime.now(timezone.utc),
        )

    def _record_failure(self, status: ConnectionStatus, message: str) -> None:
        self._last_status = DataStatus(
            provider=self.name, status=status, message=message,
            last_successful_update=self._last_status.last_successful_update,
        )

    def get_available_instruments(self) -> List[ProviderInstrument]:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(BINANCE_BASE_URL + EXCHANGE_INFO_ENDPOINT)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            self._record_failure(ConnectionStatus.DISCONNECTED, str(exc))
            raise ProviderNetworkError(f"Could not reach Binance: {exc}") from exc

        out = []
        for s in data.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            out.append(ProviderInstrument(
                symbol=s["symbol"], display_name=f"{s['baseAsset']}/{s['quoteAsset']}", category="CRYPTO",
            ))
        self._record_success()
        return out

    def get_latest_price(self, instrument: str) -> float:
        symbol = normalize_symbol(instrument)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(BINANCE_BASE_URL + TICKER_PRICE_ENDPOINT, params={"symbol": symbol})
                if resp.status_code == 400:
                    self._record_failure(ConnectionStatus.UNSUPPORTED_INSTRUMENT, f"Unknown symbol '{symbol}'.")
                    raise ProviderUnsupportedInstrumentError(f"'{symbol}' is not a known Binance symbol.")
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            self._record_failure(ConnectionStatus.DISCONNECTED, str(exc))
            raise ProviderNetworkError(f"Could not reach Binance: {exc}") from exc

        self._record_success()
        return float(data["price"])

    def get_candles(self, instrument: str, timeframe_seconds: int, limit: int) -> List[Candle]:
        try:
            candles = self._adapter.fetch_candles(instrument, timeframe_seconds, limit)
        except ValueError as exc:
            self._record_failure(ConnectionStatus.UNSUPPORTED_INSTRUMENT, str(exc))
            raise ProviderUnsupportedInstrumentError(str(exc)) from exc
        except BinanceDataFormatError as exc:
            self._record_failure(ConnectionStatus.DISCONNECTED, str(exc))
            raise ProviderNetworkError(str(exc)) from exc
        self._record_success()
        return candles

    def get_data_status(self, instrument: Optional[str] = None) -> DataStatus:
        return self._last_status
