"""Binance public market-data adapter.

Implements ``MarketDataAdapter`` for crypto pairs using Binance's PUBLIC
klines REST endpoint: https://binance-docs.github.io/apidocs/spot/en/#kline-candlestick-data

This is read-only market data only:
  * No API key, login, or account of any kind is used or required.
  * It never places, modifies, or cancels an order.
  * It has nothing to do with Pocket Option — it is simply a legitimate,
    publicly documented source of real OHLCV history for crypto pairs
    (e.g. BTC/USDT) that Pocket Option also happens to offer, so you don't
    have to transcribe candles by hand.

Binance does not have a native 2-minute kline interval, so for a 2m
request this fetches real 1-minute candles and aggregates (resamples)
pairs of them locally — it never invents a price. All other supported
timeframes (3m, 5m, 15m, 30m, 1h) map to a native Binance interval.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from .adapter import MarketDataAdapter
from .models import Candle

BINANCE_BASE_URL = "https://api.binance.com"
KLINES_ENDPOINT = "/api/v3/klines"
MAX_KLINES_PER_REQUEST = 1000
DEFAULT_TIMEOUT_SECONDS = 15.0

# Binance's native kline intervals we support, in seconds -> interval string.
NATIVE_INTERVALS: Dict[int, str] = {
    60: "1m", 180: "3m", 300: "5m", 900: "15m", 1800: "30m", 3600: "1h",
}


class BinanceDataFormatError(RuntimeError):
    """Raised when Binance is unreachable or returns something that can't
    be parsed as OHLCV. Never silently swallowed into fabricated data."""


class BinanceAdapter(MarketDataAdapter):
    """Fetches real historical OHLCV candles for a crypto pair from Binance."""

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_candles(self, asset: str, timeframe_seconds: int, limit: int) -> List[Candle]:
        symbol = normalize_symbol(asset)

        if timeframe_seconds in NATIVE_INTERVALS:
            return self._fetch_native(symbol, NATIVE_INTERVALS[timeframe_seconds], limit)

        if timeframe_seconds % 60 != 0:
            raise ValueError(f"Unsupported timeframe: {timeframe_seconds}s is not a multiple of 60s.")

        # No native Binance interval (this is the 2m case) — fetch real
        # 1-minute candles and aggregate them locally rather than inventing
        # anything.
        factor = timeframe_seconds // 60
        one_minute = self._fetch_native(symbol, "1m", limit * factor + factor)
        return resample(one_minute, factor)[-limit:]

    def _fetch_native(self, symbol: str, interval: str, limit: int) -> List[Candle]:
        candles: List[Candle] = []
        end_time_ms: Optional[int] = None
        remaining = max(limit, 1)

        with httpx.Client(timeout=self.timeout_seconds) as client:
            while remaining > 0:
                batch_limit = min(remaining, MAX_KLINES_PER_REQUEST)
                params = {"symbol": symbol, "interval": interval, "limit": batch_limit}
                if end_time_ms is not None:
                    params["endTime"] = end_time_ms

                data = self._request(client, params)
                if not data:
                    break

                # Binance includes the still-forming current candle in the
                # latest batch if no endTime was given. Drop anything not
                # yet closed — we only ever return CLOSED candles.
                now_ms = int(time.time() * 1000)
                data = [row for row in data if row[6] <= now_ms]
                if not data:
                    break

                batch = [row_to_candle(row) for row in data]
                candles = batch + candles  # older batches prepend
                remaining -= len(batch)
                end_time_ms = data[0][0] - 1  # strictly before this batch

                if len(data) < batch_limit:
                    break  # Binance has no more history to give us

        deduped = sorted({c.timestamp: c for c in candles}.values(), key=lambda c: c.timestamp)
        return deduped[-limit:]

    def _request(self, client: httpx.Client, params: dict) -> list:
        try:
            resp = client.get(BINANCE_BASE_URL + KLINES_ENDPOINT, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise BinanceDataFormatError(
                f"Binance returned {exc.response.status_code} for "
                f"{params.get('symbol')} {params.get('interval')}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise BinanceDataFormatError(f"Network error contacting Binance: {exc}") from exc

        if not isinstance(data, list):
            raise BinanceDataFormatError(f"Unexpected Binance response (not a list): {data!r}")
        return data


def normalize_symbol(asset: str) -> str:
    """Accept common spellings like 'BTC/USDT', 'btc-usdt', 'BTC_USDT'."""
    return asset.upper().replace("/", "").replace("-", "").replace("_", "").strip()


def row_to_candle(row: list) -> Candle:
    # Binance kline row: [open_time, open, high, low, close, volume, close_time, ...]
    if len(row) < 6:
        raise BinanceDataFormatError(f"Malformed kline row from Binance: {row!r}")
    return Candle(
        timestamp=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )


def resample(candles: List[Candle], factor: int) -> List[Candle]:
    """Aggregate consecutive 1-minute candles into `factor`-minute candles.

    Groups align to the start of the provided series (not wall-clock
    boundaries) — fine for this tool's ad-hoc analysis use, not meant to
    be exchange-grade bucketing. Every output candle is built only from
    real fetched candles (open=first, close=last, high=max, low=min,
    volume=sum) — nothing is invented.
    """
    out: List[Candle] = []
    for i in range(0, len(candles) - factor + 1, factor):
        group = candles[i : i + factor]
        out.append(
            Candle(
                timestamp=group[0].timestamp,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return out
