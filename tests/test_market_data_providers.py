"""Tests for the market-data provider adapter (STEP 2-10).

All HTTP is mocked — these must never depend on live network access or a
real API key. Covers: successful retrieval, invalid credentials, network
failure, rate limiting, stale/unsupported instruments, standard-vs-OTC
separation, candle aggregation, and the provider factory/config.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from instruments.providers import (
    BinanceProvider,
    ConnectionStatus,
    ProviderAuthenticationError,
    ProviderConfigError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderUnsupportedInstrumentError,
    TwelveDataProvider,
    get_configured_provider,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        pass  # TwelveDataProvider checks status_code/body itself, not via raise_for_status

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.calls.append((url, params))
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses):
    fake = FakeClient(responses)
    monkeypatch.setattr("instruments.providers.twelvedata.httpx.Client", lambda timeout: fake)
    return fake


def _td_row(dt, o, h, l, c, v=100):
    return {"datetime": dt, "open": str(o), "high": str(h), "low": str(l), "close": str(c), "volume": str(v)}


# --- Provider factory / config -------------------------------------------
def test_factory_returns_none_when_unconfigured():
    assert get_configured_provider({}) is None


def test_factory_requires_api_key_for_twelvedata():
    with pytest.raises(ProviderConfigError):
        get_configured_provider({"MARKET_DATA_PROVIDER": "twelvedata"})


def test_factory_rejects_unknown_provider():
    with pytest.raises(ProviderConfigError):
        get_configured_provider({"MARKET_DATA_PROVIDER": "madeup"})


def test_factory_builds_binance_without_api_key():
    p = get_configured_provider({"MARKET_DATA_PROVIDER": "binance"})
    assert isinstance(p, BinanceProvider)


def test_factory_builds_twelvedata_with_api_key():
    p = get_configured_provider({"MARKET_DATA_PROVIDER": "twelvedata", "MARKET_DATA_API_KEY": "k"})
    assert isinstance(p, TwelveDataProvider)
    assert p.api_key == "k"


def test_factory_respects_custom_base_url():
    p = get_configured_provider({
        "MARKET_DATA_PROVIDER": "twelvedata", "MARKET_DATA_API_KEY": "k",
        "MARKET_DATA_BASE_URL": "https://proxy.example.com",
    })
    assert p.base_url == "https://proxy.example.com"


# --- TwelveDataProvider: successful retrieval -------------------------
def test_get_candles_native_interval_success(monkeypatch):
    rows = [_td_row(f"2026-01-01 {i // 4:02d}:{(i % 4) * 15:02d}:00", 1.08, 1.081, 1.079, 1.0805) for i in range(20)]
    fake = _patch_client(monkeypatch, [FakeResponse({"values": rows, "status": "ok"})])
    # 1 minute after the last (04:45) candle closed at 05:00
    provider = TwelveDataProvider(api_key="k", clock=lambda: datetime(2026, 1, 1, 5, 1, tzinfo=timezone.utc))
    candles = provider.get_candles("EURUSD", 900, 15)  # 15m native
    assert len(candles) == 15
    assert fake.calls[0][1]["interval"] == "15min"
    # ascending order (Twelve Data returns most-recent-first)
    for a, b in zip(candles, candles[1:]):
        assert a.timestamp < b.timestamp
    assert provider.get_data_status().status == ConnectionStatus.CONNECTED


def test_get_latest_price_success(monkeypatch):
    fake = _patch_client(monkeypatch, [FakeResponse({"price": "1.0855"})])
    provider = TwelveDataProvider(api_key="k")
    price = provider.get_latest_price("EURUSD")
    assert price == 1.0855
    assert fake.calls[0][1]["symbol"] == "EUR/USD"


def test_get_available_instruments_success(monkeypatch):
    payload = {"data": [
        {"symbol": "EUR/USD", "currency_base": "Euro", "currency_quote": "US Dollar"},
        {"symbol": "GBP/USD", "currency_base": "British Pound", "currency_quote": "US Dollar"},
    ]}
    _patch_client(monkeypatch, [FakeResponse(payload)])
    provider = TwelveDataProvider(api_key="k")
    instruments = provider.get_available_instruments()
    assert len(instruments) == 2
    assert instruments[0].symbol == "EUR/USD"
    assert instruments[0].category == "FOREX"


# --- Error handling: invalid credentials, network failure, rate limit --
def test_invalid_api_key_raises_authentication_error(monkeypatch):
    _patch_client(monkeypatch, [FakeResponse({"code": 401, "message": "Invalid apikey", "status": "error"}, status_code=401)])
    provider = TwelveDataProvider(api_key="bad-key")
    with pytest.raises(ProviderAuthenticationError):
        provider.get_latest_price("EURUSD")
    assert provider.get_data_status().status == ConnectionStatus.AUTHENTICATION_ERROR


def test_rate_limit_raises_rate_limit_error(monkeypatch):
    _patch_client(monkeypatch, [FakeResponse({"code": 429, "message": "Too many requests", "status": "error"}, status_code=429)])
    provider = TwelveDataProvider(api_key="k")
    with pytest.raises(ProviderRateLimitError):
        provider.get_latest_price("EURUSD")
    assert provider.get_data_status().status == ConnectionStatus.RATE_LIMIT


def test_unsupported_symbol_raises_unsupported_instrument_error(monkeypatch):
    _patch_client(monkeypatch, [FakeResponse({"code": 400, "message": "symbol not found", "status": "error"}, status_code=400)])
    provider = TwelveDataProvider(api_key="k")
    with pytest.raises(ProviderUnsupportedInstrumentError):
        provider.get_latest_price("NOTAPAIR")
    assert provider.get_data_status().status == ConnectionStatus.UNSUPPORTED_INSTRUMENT


def test_network_failure_raises_provider_network_error(monkeypatch):
    class RaisingClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None): raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("instruments.providers.twelvedata.httpx.Client", lambda timeout: RaisingClient())
    provider = TwelveDataProvider(api_key="k")
    with pytest.raises(ProviderNetworkError):
        provider.get_latest_price("EURUSD")
    assert provider.get_data_status().status == ConnectionStatus.DISCONNECTED


def test_otc_instrument_is_rejected_before_any_request(monkeypatch):
    fake = _patch_client(monkeypatch, [])  # no responses queued — must not be called
    provider = TwelveDataProvider(api_key="k")
    with pytest.raises(ProviderUnsupportedInstrumentError):
        provider.get_candles("EURUSD_OTC", 180, 10)
    assert len(fake.calls) == 0  # never even attempted a network call


# --- Candle aggregation (2m/3m from native 1min) -------------------------
def test_2m_candles_aggregated_from_native_1min(monkeypatch):
    rows = [_td_row(f"2026-01-01 00:{i:02d}:00", 1 + i * 0.001, 1.002 + i * 0.001, 0.999 + i * 0.001, 1.001 + i * 0.001)
            for i in range(8)]
    fake = _patch_client(monkeypatch, [FakeResponse({"values": rows, "status": "ok"})])
    provider = TwelveDataProvider(api_key="k")
    candles = provider.get_candles("EURUSD", 120, 4)  # 2m, not native
    assert fake.calls[0][1]["interval"] == "1min"
    assert len(candles) == 4
    for c in candles:
        assert c.high >= c.low


def test_3m_aggregation_drops_incomplete_trailing_group(monkeypatch):
    # 7 one-minute candles -> only 2 complete 3-minute groups
    rows = [_td_row(f"2026-01-01 00:{i:02d}:00", 1, 1.01, 0.99, 1.0) for i in range(7)]
    _patch_client(monkeypatch, [FakeResponse({"values": rows, "status": "ok"})])
    provider = TwelveDataProvider(api_key="k")
    candles = provider.get_candles("EURUSD", 180, 10)
    assert len(candles) == 2


def test_unsupported_timeframe_raises_value_error():
    provider = TwelveDataProvider(api_key="k")
    with pytest.raises(ValueError):
        provider.get_candles("EURUSD", 125, 10)


# --- BinanceProvider wrapper (delegates to the existing, tested adapter) --
def test_binance_provider_get_candles_delegates_to_adapter(monkeypatch):
    from data.models import Candle
    sentinel = [Candle(timestamp=datetime.now(timezone.utc), open=1, high=2, low=0.5, close=1.5, volume=10)]
    provider = BinanceProvider()
    monkeypatch.setattr(provider._adapter, "fetch_candles", lambda *a, **kw: sentinel)
    result = provider.get_candles("BTCUSDT", 180, 5)
    assert result == sentinel
    assert provider.get_data_status().status == ConnectionStatus.CONNECTED


def test_binance_provider_unsupported_timeframe_mapped_to_error(monkeypatch):
    provider = BinanceProvider()
    monkeypatch.setattr(provider._adapter, "fetch_candles", lambda *a, **kw: (_ for _ in ()).throw(ValueError("bad timeframe")))
    with pytest.raises(ProviderUnsupportedInstrumentError):
        provider.get_candles("BTCUSDT", 125, 5)
    assert provider.get_data_status().status == ConnectionStatus.UNSUPPORTED_INSTRUMENT


def test_binance_provider_latest_price_success(monkeypatch):
    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"price": "50000.12"}
    class FakeClient2:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None): return FakeResp()

    monkeypatch.setattr("instruments.providers.binance_provider.httpx.Client", lambda timeout: FakeClient2())
    provider = BinanceProvider()
    assert provider.get_latest_price("BTCUSDT") == 50000.12


def test_binance_provider_unknown_symbol_price(monkeypatch):
    class FakeResp:
        status_code = 400
        text = "Invalid symbol"
        def raise_for_status(self): pass
        def json(self): return {}
    class FakeClient2:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None): return FakeResp()

    monkeypatch.setattr("instruments.providers.binance_provider.httpx.Client", lambda timeout: FakeClient2())
    provider = BinanceProvider()
    with pytest.raises(ProviderUnsupportedInstrumentError):
        provider.get_latest_price("NOTREAL")


# --- Streaming explicitly not fabricated ---------------------------------
def test_subscribe_to_candles_not_implemented_for_either_provider():
    with pytest.raises(NotImplementedError):
        TwelveDataProvider(api_key="k").subscribe_to_candles("EURUSD", 120)
    with pytest.raises(NotImplementedError):
        BinanceProvider().subscribe_to_candles("BTCUSDT", 120)
