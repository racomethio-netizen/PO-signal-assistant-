"""Twelve Data tests built from responses shaped like the REAL API, not
idealized fixtures: rows arrive newest-first, forex rows carry no volume
field, and the newest row is the still-forming current candle. Every
HTTP call is mocked — no network access or real API key is needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.state as state_module
from api.main import app
from instruments.providers import ConnectionStatus, ProviderUnsupportedInstrumentError, TwelveDataProvider
from tests.conftest import deterministic_series


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

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


def realistic_rows(last_start: datetime, count: int, step_seconds: int = 60, base: float = 1.0850):
    """`count` forex rows ending with the candle that STARTS at
    `last_start`, newest-first, without a volume key — the real shape."""
    rows = []
    price = base
    for i in range(count):
        ts = last_start - timedelta(seconds=step_seconds * (count - 1 - i))
        wobble = ((i % 10) - 5) / 5.0 * 0.0004
        o, c = price, price + 0.00002 + wobble
        rows.append({
            "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": f"{o:.5f}", "high": f"{max(o, c) + 0.0001:.5f}",
            "low": f"{min(o, c) - 0.0001:.5f}", "close": f"{c:.5f}",
        })
        price = c
    rows.reverse()
    return rows


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def minute_rows(minutes, day=utc(2026, 11, 6, 10, 0)):
    """Newest-first rows for the given minute offsets after `day`, with
    distinct, valid prices per minute so aggregation can be checked."""
    rows = []
    for m in sorted(minutes, reverse=True):
        o = 1.0 + m * 0.001
        rows.append({
            "datetime": (day + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": f"{o:.5f}", "high": f"{o + 0.0007:.5f}",
            "low": f"{o - 0.0002:.5f}", "close": f"{o + 0.0005:.5f}",
        })
    return rows


def provider_at(now):
    return TwelveDataProvider(api_key="k", clock=lambda: now)


# --- Closed candles only -----------------------------------------------
def test_native_15m_drops_the_still_forming_candle(monkeypatch):
    rows = realistic_rows(utc(2026, 11, 6, 10, 45), 30, step_seconds=900)  # 10:45 candle forms until 11:00
    fake = _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": rows})])
    candles = provider_at(utc(2026, 11, 6, 10, 52)).get_candles("EURUSD", 900, 20)
    assert fake.calls[0][1]["outputsize"] == 21  # one extra to make up for the dropped forming candle
    assert len(candles) == 20
    assert candles[-1].timestamp == utc(2026, 11, 6, 10, 30)
    assert all(c.volume == 0.0 for c in candles)  # forex rows have no volume field


# --- 2m / 3m aggregation -----------------------------------------------
def test_2m_candles_align_to_even_minutes_even_when_data_starts_on_odd_minute(monkeypatch):
    fake = _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": minute_rows(range(1, 8))})])
    candles = provider_at(utc(2026, 11, 6, 10, 7, 30)).get_candles("EURUSD", 120, 10)
    assert fake.calls[0][1]["interval"] == "1min"
    # 10:01 alone can't form the 10:00 candle; 10:06 is incomplete because 10:07 is still forming.
    assert [c.timestamp for c in candles] == [utc(2026, 11, 6, 10, 2), utc(2026, 11, 6, 10, 4)]
    first = candles[0]
    assert first.open == pytest.approx(1.002)
    assert first.close == pytest.approx(1.0035)
    assert first.high == pytest.approx(1.0037)
    assert first.low == pytest.approx(1.0018)


def test_bucket_with_a_missing_minute_is_dropped_not_padded(monkeypatch):
    minutes = [m for m in range(8) if m != 3]
    _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": minute_rows(minutes)})])
    candles = provider_at(utc(2026, 11, 6, 10, 8, 30)).get_candles("EURUSD", 120, 10)
    assert [c.timestamp.minute for c in candles] == [0, 4, 6]


def test_3m_candles_align_to_multiples_of_three_minutes(monkeypatch):
    fake = _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": minute_rows(range(11))})])
    candles = provider_at(utc(2026, 11, 6, 10, 11)).get_candles("EURUSD", 180, 10)
    assert fake.calls[0][1]["outputsize"] == (10 + 2) * 3
    assert [c.timestamp.minute for c in candles] == [0, 3, 6]


# --- Status reporting --------------------------------------------------
def test_old_data_is_flagged_stale_but_still_returned(monkeypatch):
    friday_close = utc(2026, 11, 6, 21, 58)
    rows = realistic_rows(friday_close, 50, step_seconds=900)
    _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": rows})])
    provider = provider_at(utc(2026, 11, 7, 12, 0))  # Saturday
    candles = provider.get_candles("EURUSD", 900, 20)
    assert len(candles) == 20
    status = provider.get_data_status()
    assert status.status == ConnectionStatus.STALE_DATA
    assert "market is probably closed" in status.message


def test_fresh_data_reports_connected(monkeypatch):
    rows = realistic_rows(utc(2026, 11, 6, 10, 45), 30, step_seconds=900)
    _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": rows})])
    provider = provider_at(utc(2026, 11, 6, 11, 0, 5))
    provider.get_candles("EURUSD", 900, 20)
    assert provider.get_data_status().status == ConnectionStatus.CONNECTED


def test_symbol_not_found_404_maps_to_unsupported_instrument(monkeypatch):
    _patch_client(monkeypatch, [FakeResponse({"code": 404, "message": "symbol not found", "status": "error"})])
    provider = provider_at(utc(2026, 11, 6, 10, 0))
    with pytest.raises(ProviderUnsupportedInstrumentError):
        provider.get_candles("ABCXYZ", 900, 10)
    assert provider.get_data_status().status == ConnectionStatus.UNSUPPORTED_INSTRUMENT


def test_connection_test_success(monkeypatch):
    fake = _patch_client(monkeypatch, [FakeResponse({"price": "1.08550"})])
    status = provider_at(utc(2026, 11, 6, 10, 0)).test_connection()
    assert status.status == ConnectionStatus.CONNECTED
    assert fake.calls[0][1]["symbol"] == "EUR/USD"


def test_connection_test_reports_bad_key_without_raising(monkeypatch):
    # Twelve Data reports a bad key as HTTP 200 with an error body.
    _patch_client(monkeypatch, [FakeResponse(
        {"code": 401, "message": "**apikey** parameter is incorrect or not specified.", "status": "error"},
    )])
    status = provider_at(utc(2026, 11, 6, 10, 0)).test_connection()
    assert status.status == ConnectionStatus.AUTHENTICATION_ERROR
    assert "apikey" in status.message


# --- API: status endpoint probes so the pill shows the real state ------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "twelvedata")
    monkeypatch.setenv("MARKET_DATA_API_KEY", "fake-key")
    state_module._state = None
    return TestClient(app)


def test_live_fetch_then_signal_is_not_blocked_by_in_progress_candle(client, monkeypatch):
    current_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": realistic_rows(current_minute, 405)})])

    resp = client.post("/api/data/fetch/provider", json={"asset": "EURUSD", "timeframe": "2m", "limit": 200})
    assert resp.status_code == 200, resp.text

    signal = client.post("/api/signal/generate", json={"asset": "EURUSD", "timeframe": "2m"}).json()["signal"]
    assert "DATA UNAVAILABLE — NO SIGNAL" not in signal["reasons_against"]

    status = client.get("/api/data/status", params={"asset": "EURUSD", "timeframe": "2m"}).json()
    assert status["source"] == "twelvedata"


def _upload_eurusd_2m(client):
    candles = [
        {"timestamp": c.timestamp.isoformat(), "open": c.open, "high": c.high, "low": c.low, "close": c.close}
        for c in deterministic_series(70, start_price=1.08, amplitude=0.0005)
    ]
    assert client.post("/api/data/upload/json", json={"asset": "EURUSD", "timeframe": "2m", "candles": candles}).status_code == 200
    return candles


def test_uploaded_candles_are_marked_as_uploads(client):
    _upload_eurusd_2m(client)
    status = client.get("/api/data/status", params={"asset": "EURUSD", "timeframe": "2m"}).json()
    assert status["source"] == "upload"


def test_automatic_fetch_keeps_uploaded_candles(client, monkeypatch):
    uploaded = _upload_eurusd_2m(client)
    current_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": realistic_rows(current_minute, 405)})])
    body = client.post("/api/data/fetch/provider", json={
        "asset": "EURUSD", "timeframe": "2m", "limit": 200, "keep_uploads": True,
    }).json()
    assert body["kept_upload"] is True
    assert body["candle_count"] == len(uploaded)
    status = client.get("/api/data/status", params={"asset": "EURUSD", "timeframe": "2m"}).json()
    assert status["source"] == "upload"


def test_explicit_fetch_still_replaces_uploads(client, monkeypatch):
    _upload_eurusd_2m(client)
    current_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": realistic_rows(current_minute, 405)})])
    client.post("/api/data/fetch/provider", json={"asset": "EURUSD", "timeframe": "2m", "limit": 200})
    status = client.get("/api/data/status", params={"asset": "EURUSD", "timeframe": "2m"}).json()
    assert status["source"] == "twelvedata"


def test_uploaded_and_live_candles_never_confirm_each_other(client, monkeypatch):
    _upload_eurusd_2m(client)  # EURUSD 2m: uploaded (January)
    current_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    _patch_client(monkeypatch, [FakeResponse({"status": "ok", "values": realistic_rows(current_minute, 300, step_seconds=900)})])
    client.post("/api/data/fetch/provider", json={"asset": "EURUSD", "timeframe": "15m", "limit": 200})  # live
    state = state_module.get_state()
    assert state.get_confirmation_candles("EURUSD", "2m") == {}
    assert state.get_confirmation_candles("EURUSD", "15m") == {}


def test_status_probes_once_when_provider_never_called(client, monkeypatch):
    fake = _patch_client(monkeypatch, [FakeResponse({"price": "1.08550"})])
    first = client.get("/api/market-data/status").json()
    assert first["status"] == "CONNECTED"
    assert first["provider"] == "twelvedata"
    second = client.get("/api/market-data/status").json()
    assert second["status"] == "CONNECTED"
    assert len(fake.calls) == 1  # no API credit spent on every dashboard poll


def test_status_probe_param_forces_a_fresh_check(client, monkeypatch):
    fake = _patch_client(monkeypatch, [
        FakeResponse({"price": "1.08550"}),
        FakeResponse({"code": 429, "message": "You have run out of API credits for the current minute.", "status": "error"}),
    ])
    client.get("/api/market-data/status")
    forced = client.get("/api/market-data/status?probe=true").json()
    assert len(fake.calls) == 2
    assert forced["status"] == "RATE LIMIT"
    assert "API credits" in forced["message"]


def test_status_reports_bad_key_immediately(client, monkeypatch):
    _patch_client(monkeypatch, [FakeResponse({"code": 401, "message": "**apikey** parameter is incorrect", "status": "error"})])
    body = client.get("/api/market-data/status").json()
    assert body["status"] == "AUTHENTICATION ERROR"
