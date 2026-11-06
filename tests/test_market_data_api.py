"""API-level tests for the generic market-data provider endpoints."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api.state as state_module
from api.main import app
from instruments.providers import ConnectionStatus, ProviderUnsupportedInstrumentError
from tests.conftest import deterministic_series


@pytest.fixture
def client():
    state_module._state = None
    return TestClient(app)


def test_status_reports_unconfigured_by_default(client, monkeypatch):
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    resp = client.get("/api/market-data/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["status"] == "DISCONNECTED"


def test_fetch_provider_503_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    resp = client.post("/api/data/fetch/provider", json={"asset": "EURUSD", "timeframe": "3m", "limit": 50})
    assert resp.status_code == 503


def test_fetch_provider_success_with_twelvedata(client, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "twelvedata")
    monkeypatch.setenv("MARKET_DATA_API_KEY", "fake-key")
    candles = deterministic_series(90, start_price=1.08, drift=0.0003, amplitude=0.0005, step_seconds=180)

    with patch("instruments.providers.twelvedata.TwelveDataProvider.get_candles", return_value=candles):
        resp = client.post("/api/data/fetch/provider", json={"asset": "EURUSD", "timeframe": "3m", "limit": 90})
    assert resp.status_code == 200
    body = resp.json()
    assert body["candle_count"] == 90

    inst = client.get("/api/instruments/EURUSD").json()
    assert inst["data_source"] == "twelvedata"  # upgraded from the default 'manual'


def test_fetch_provider_rejects_otc_before_network_call(client, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "twelvedata")
    monkeypatch.setenv("MARKET_DATA_API_KEY", "fake-key")
    with patch("instruments.providers.twelvedata.TwelveDataProvider.get_candles") as mock_fetch:
        resp = client.post("/api/data/fetch/provider", json={"asset": "EURUSD_OTC", "timeframe": "3m", "limit": 50})
    assert resp.status_code == 400
    mock_fetch.assert_not_called()


def test_fetch_provider_rejects_instrument_category_twelvedata_does_not_supply(client, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "twelvedata")
    monkeypatch.setenv("MARKET_DATA_API_KEY", "fake-key")
    client.post("/api/instruments/register", json={
        "symbol": "XAUUSD", "display_name": "Gold", "base_currency": "XAU", "quote_currency": "USD",
        "category": "OTHER", "instrument_type": "STANDARD", "data_source": "manual",
    })
    with patch("instruments.providers.twelvedata.TwelveDataProvider.get_candles") as mock_fetch:
        resp = client.post("/api/data/fetch/provider", json={"asset": "XAUUSD", "timeframe": "3m", "limit": 50})
    assert resp.status_code == 400
    assert "CRYPTO and FOREX" in resp.json()["detail"]
    mock_fetch.assert_not_called()


def test_fetch_provider_unsupported_instrument_maps_to_400(client, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "twelvedata")
    monkeypatch.setenv("MARKET_DATA_API_KEY", "fake-key")
    with patch(
        "instruments.providers.twelvedata.TwelveDataProvider.get_candles",
        side_effect=ProviderUnsupportedInstrumentError("nope"),
    ):
        resp = client.post("/api/data/fetch/provider", json={"asset": "XYZUSD", "timeframe": "3m", "limit": 50})
    assert resp.status_code == 400


def test_instruments_catalogue_endpoint_requires_provider(client, monkeypatch):
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    resp = client.get("/api/market-data/instruments")
    assert resp.status_code == 503


def test_misconfigured_provider_returns_clear_error(client, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "twelvedata")
    monkeypatch.delenv("MARKET_DATA_API_KEY", raising=False)
    resp = client.post("/api/data/fetch/provider", json={"asset": "EURUSD", "timeframe": "3m", "limit": 50})
    assert resp.status_code == 500
    assert "MARKET_DATA_API_KEY" in resp.json()["detail"]
