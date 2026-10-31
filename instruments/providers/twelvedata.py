"""Twelve Data as a MarketDataProvider — the forex-capable reference
implementation for STEP 2/3 of the market-data adapter upgrade.

Public API, documented at https://twelvedata.com/docs. Read-only: an API
key authenticates requests to Twelve Data, never to Pocket Option or any
broker, and nothing here can place an order.

Twelve Data has no native 2-minute or 3-minute interval (its intervals
are 1min/5min/15min/30min/45min/1h/2h/4h/1day/1week/1month) — those two
timeframes are built by fetching real 1-minute candles and aggregating
them with the SAME ``resample()`` already used (and tested) for Binance's
2-minute handling, reused as-is rather than reimplemented.

Twelve Data has no concept of OTC instruments at all, so any OTC-flagged
instrument is rejected outright here rather than silently served
standard-market data under an OTC label.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from data.binance_adapter import resample
from data.models import Candle

from .base import (
    ConnectionStatus,
    DataStatus,
    MarketDataProvider,
    ProviderAuthenticationError,
    ProviderInstrument,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderUnsupportedInstrumentError,
)

DEFAULT_BASE_URL = "https://api.twelvedata.com"
FOREX_PAIRS_ENDPOINT = "/forex_pairs"
PRICE_ENDPOINT = "/price"
TIME_SERIES_ENDPOINT = "/time_series"

NATIVE_INTERVALS: Dict[int, str] = {
    60: "1min", 300: "5min", 900: "15min", 1800: "30min", 3600: "1h", 7200: "2h", 14400: "4h",
}


def _to_td_symbol(instrument: str) -> str:
    """'EURUSD' -> 'EUR/USD'; already-slashed input passes through."""
    s = instrument.upper().replace("_OTC", "")
    if "/" in s:
        return s
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    return s


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    DEFAULT_BASE_URL = DEFAULT_BASE_URL

    def __init__(self, api_key: str, base_url: Optional[str] = None, timeout_seconds: float = 15.0) -> None:
        if not api_key:
            raise ValueError("TwelveDataProvider requires an api_key (MARKET_DATA_API_KEY).")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._last_status = DataStatus(
            provider=self.name, status=ConnectionStatus.DISCONNECTED,
            message="No request made yet.",
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

    def _request(self, client: httpx.Client, path: str, params: dict) -> dict:
        params = {**params, "apikey": self.api_key}
        try:
            resp = client.get(self.base_url + path, params=params)
        except httpx.HTTPError as exc:
            self._record_failure(ConnectionStatus.DISCONNECTED, str(exc))
            raise ProviderNetworkError(f"Network error contacting Twelve Data: {exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            self._record_failure(ConnectionStatus.DISCONNECTED, "Non-JSON response from Twelve Data.")
            raise ProviderNetworkError("Twelve Data returned a non-JSON response.") from exc

        # Twelve Data signals errors either via HTTP status or a
        # {"status": "error", "code": ...} body even on HTTP 200.
        error_code = resp.status_code if resp.status_code >= 400 else data.get("code")
        if data.get("status") == "error" or resp.status_code >= 400:
            message = data.get("message", resp.text[:200])
            if error_code in (401, 403):
                self._record_failure(ConnectionStatus.AUTHENTICATION_ERROR, message)
                raise ProviderAuthenticationError(f"Twelve Data authentication failed: {message}")
            if error_code == 429:
                self._record_failure(ConnectionStatus.RATE_LIMIT, message)
                raise ProviderRateLimitError(f"Twelve Data rate limit hit: {message}")
            if error_code == 400:
                self._record_failure(ConnectionStatus.UNSUPPORTED_INSTRUMENT, message)
                raise ProviderUnsupportedInstrumentError(f"Twelve Data rejected the request: {message}")
            self._record_failure(ConnectionStatus.DISCONNECTED, message)
            raise ProviderNetworkError(f"Twelve Data error: {message}")

        self._record_success()
        return data

    def get_available_instruments(self) -> List[ProviderInstrument]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            data = self._request(client, FOREX_PAIRS_ENDPOINT, {})
        out = []
        for row in data.get("data", []):
            symbol = row.get("symbol")
            if not symbol:
                continue
            base = row.get("currency_base", "")
            quote = row.get("currency_quote", "")
            out.append(ProviderInstrument(symbol=symbol, display_name=f"{symbol} ({base}/{quote})", category="FOREX"))
        return out

    def get_latest_price(self, instrument: str) -> float:
        symbol = _to_td_symbol(instrument)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            data = self._request(client, PRICE_ENDPOINT, {"symbol": symbol})
        return float(data["price"])

    def get_candles(self, instrument: str, timeframe_seconds: int, limit: int) -> List[Candle]:
        symbol = _to_td_symbol(instrument)
        if "_OTC" in instrument.upper():
            raise ProviderUnsupportedInstrumentError(
                "Twelve Data has no OTC market data; an OTC instrument can never be fetched from it."
            )

        if timeframe_seconds in NATIVE_INTERVALS:
            return self._fetch_native(symbol, NATIVE_INTERVALS[timeframe_seconds], limit)

        if timeframe_seconds % 60 != 0:
            raise ValueError(f"Unsupported timeframe: {timeframe_seconds}s is not a multiple of 60s.")
        factor = timeframe_seconds // 60
        one_minute = self._fetch_native(symbol, "1min", limit * factor + factor)
        return resample(one_minute, factor)[-limit:]

    def _fetch_native(self, symbol: str, interval: str, limit: int) -> List[Candle]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            data = self._request(client, TIME_SERIES_ENDPOINT, {
                "symbol": symbol, "interval": interval, "outputsize": min(limit, 5000),
                "timezone": "UTC",
            })

        values = data.get("values", [])
        candles = []
        for row in values:
            ts = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            candles.append(Candle(
                timestamp=ts,
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            ))
        # Twelve Data returns most-recent-first; the rest of this project
        # expects ascending (oldest-first) order.
        candles.sort(key=lambda c: c.timestamp)
        return candles[-limit:]

    def get_data_status(self, instrument: Optional[str] = None) -> DataStatus:
        return self._last_status
