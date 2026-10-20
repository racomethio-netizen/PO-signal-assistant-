"""Tests for the Binance public market-data adapter.

All HTTP calls are mocked — these tests must never depend on live network
access (so they're deterministic and work in CI). They verify: symbol
normalization, native-interval parsing, 1m->2m resampling (since Binance
has no native 2m interval), pagination, unclosed-candle filtering, and
error handling on bad HTTP responses / malformed payloads.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from data.binance_adapter import (
    BinanceAdapter,
    BinanceDataFormatError,
    normalize_symbol,
    resample,
    row_to_candle,
)


def test_normalize_symbol_variants():
    assert normalize_symbol("BTC/USDT") == "BTCUSDT"
    assert normalize_symbol("btc-usdt") == "BTCUSDT"
    assert normalize_symbol("BTC_USDT") == "BTCUSDT"
    assert normalize_symbol(" btcusdt ") == "BTCUSDT"


def _kline_row(open_time_ms, o, h, l, c, v, close_time_ms=None):
    if close_time_ms is None:
        close_time_ms = open_time_ms + 59_999
    return [open_time_ms, str(o), str(h), str(l), str(c), str(v), close_time_ms,
            "0", 0, "0", "0", "0"]


def test_row_to_candle_parses_fields():
    row = _kline_row(1_700_000_000_000, 100.0, 101.0, 99.5, 100.5, 12.3)
    c = row_to_candle(row)
    assert c.open == 100.0 and c.high == 101.0 and c.low == 99.5
    assert c.close == 100.5 and c.volume == 12.3


def test_row_to_candle_malformed_raises():
    with pytest.raises(BinanceDataFormatError):
        row_to_candle([1, 2, 3])


def test_resample_aggregates_pairs_correctly():
    base = 1_700_000_000_000
    candles = [row_to_candle(_kline_row(base + i * 60_000, 100 + i, 100 + i + 0.5, 100 + i - 0.5, 100 + i + 0.2, 1.0)) for i in range(4)]
    out = resample(candles, 2)
    assert len(out) == 2
    first = out[0]
    assert first.open == candles[0].open
    assert first.close == candles[1].close
    assert first.high == max(candles[0].high, candles[1].high)
    assert first.low == min(candles[0].low, candles[1].low)
    assert first.volume == candles[0].volume + candles[1].volume


def test_resample_drops_incomplete_trailing_group():
    base = 1_700_000_000_000
    candles = [row_to_candle(_kline_row(base + i * 60_000, 1, 1, 1, 1, 1)) for i in range(3)]
    out = resample(candles, 2)
    assert len(out) == 1  # the 3rd lone candle doesn't form a full pair


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.binance.com/api/v3/klines")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


class FakeClient:
    """Mimics httpx.Client's context-manager + .get() surface."""

    def __init__(self, responses):
        # responses: list of FakeResponse, returned in call order
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.calls.append(params)
        return self._responses.pop(0)


def _patched_client(monkeypatch, responses):
    fake = FakeClient(responses)
    monkeypatch.setattr("data.binance_adapter.httpx.Client", lambda timeout: fake)
    return fake


def test_fetch_native_interval_single_batch(monkeypatch):
    now_ms = int(time.time() * 1000)
    rows = [_kline_row(now_ms - (i + 1) * 180_000, i, i + 1, i - 1, i + 0.5, 1, close_time_ms=now_ms - (i) * 180_000 - 1)
            for i in reversed(range(5))]
    fake = _patched_client(monkeypatch, [FakeResponse(rows)])

    adapter = BinanceAdapter()
    candles = adapter.fetch_candles("BTC/USDT", 180, 5)  # 3m native
    assert len(candles) == 5
    assert fake.calls[0]["interval"] == "3m"
    assert fake.calls[0]["symbol"] == "BTCUSDT"


def test_fetch_2m_resamples_from_native_1m(monkeypatch):
    now_ms = int(time.time() * 1000)
    # 8 one-minute candles -> should resample into 4 two-minute candles
    rows = []
    for i in range(8):
        open_time = now_ms - (8 - i) * 60_000
        rows.append(_kline_row(open_time, i, i + 1, i - 1, i + 0.5, 1, close_time_ms=open_time + 59_999))
    fake = _patched_client(monkeypatch, [FakeResponse(rows)])

    adapter = BinanceAdapter()
    candles = adapter.fetch_candles("ETHUSDT", 120, 4)  # 2m, not native
    assert fake.calls[0]["interval"] == "1m"
    assert len(candles) == 4


def test_unclosed_current_candle_is_filtered_out(monkeypatch):
    now_ms = int(time.time() * 1000)
    closed = _kline_row(now_ms - 180_000, 1, 2, 0.5, 1.5, 1, close_time_ms=now_ms - 1)
    still_forming = _kline_row(now_ms, 1, 2, 0.5, 1.5, 1, close_time_ms=now_ms + 10 * 60_000)
    fake = _patched_client(monkeypatch, [FakeResponse([closed, still_forming])])

    adapter = BinanceAdapter()
    candles = adapter.fetch_candles("BTCUSDT", 180, 5)
    assert len(candles) == 1  # the still-forming candle must be dropped


def test_http_error_raises_binance_data_format_error(monkeypatch):
    _patched_client(monkeypatch, [FakeResponse({"msg": "Invalid symbol."}, status_code=400)])
    adapter = BinanceAdapter()
    with pytest.raises(BinanceDataFormatError):
        adapter.fetch_candles("NOTASYMBOL", 180, 5)


def test_malformed_non_list_response_raises(monkeypatch):
    _patched_client(monkeypatch, [FakeResponse({"not": "a list"})])
    adapter = BinanceAdapter()
    with pytest.raises(BinanceDataFormatError):
        adapter.fetch_candles("BTCUSDT", 180, 5)


def test_unsupported_timeframe_raises_value_error(monkeypatch):
    adapter = BinanceAdapter()
    with pytest.raises(ValueError):
        adapter.fetch_candles("BTCUSDT", 125, 5)  # not a multiple of 60s


def test_pagination_merges_two_batches(monkeypatch):
    now_ms = int(time.time() * 1000)

    def make_batch(start_i, count):
        rows = []
        for i in range(count):
            idx = start_i + i
            open_time = now_ms - (count - i) * 60_000 - start_i * 60_000
            rows.append(_kline_row(open_time, idx, idx + 1, idx - 1, idx + 0.5, 1, close_time_ms=open_time + 59_999))
        return rows

    # A real first ("newest") call always fills up to the per-request cap when
    # recent history exists; genuine pagination only kicks in once the total
    # requested exceeds that cap. Lower the cap here so 2 calls of 3 fill a
    # 5-candle request, matching that real behavior instead of simulating an
    # artificially short first page (which the adapter would otherwise read
    # as "no more history" and correctly stop after).
    monkeypatch.setattr("data.binance_adapter.MAX_KLINES_PER_REQUEST", 3)
    newest_batch = make_batch(2, 3)  # full page -> loop continues
    older_batch = make_batch(0, 2)   # remaining 2 -> loop ends
    fake = _patched_client(monkeypatch, [FakeResponse(newest_batch), FakeResponse(older_batch)])

    adapter = BinanceAdapter()
    candles = adapter._fetch_native("BTCUSDT", "1m", 5)
    assert len(fake.calls) == 2
    assert "endTime" not in fake.calls[0]
    assert "endTime" in fake.calls[1]
    assert len(candles) == 5
    # strictly ascending timestamps after merge
    for a, b in zip(candles, candles[1:]):
        assert a.timestamp < b.timestamp
