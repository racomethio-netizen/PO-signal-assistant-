import pytest
from fastapi.testclient import TestClient

import api.state as state_module
from api.main import app
from api.rate_limit import RateLimiter
from tests.conftest import deterministic_series


@pytest.fixture
def client():
    state_module._state = None  # fresh in-memory state per test
    return TestClient(app)


def candles_to_rows(candles):
    return [
        {
            "timestamp": c.timestamp.isoformat(),
            "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume,
        }
        for c in candles
    ]


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "PAPER"


def test_signal_generate_without_data_returns_no_trade(client):
    resp = client.post("/api/signal/generate", json={"asset": "NEW/ASSET", "timeframe": "2m"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["signal"]["direction"] == "NO TRADE"
    assert "DATA UNAVAILABLE" in body["signal"]["reasons_against"][0]


def test_upload_manual_then_generate_signal(client):
    candles = deterministic_series(80, start_price=100.0, drift=0.3, amplitude=0.3)
    rows = candles_to_rows(candles)
    upload = client.post(
        "/api/data/upload/manual",
        json={"asset": "EURUSD", "timeframe": "2m", "rows": rows},
    )
    assert upload.status_code == 200
    assert upload.json()["candle_count"] == 80

    gen = client.post("/api/signal/generate", json={"asset": "EURUSD", "timeframe": "2m"})
    assert gen.status_code == 200
    sig = gen.json()["signal"]
    assert sig["direction"] != ""
    assert "risk_warning" in sig


def test_upload_manual_rejects_invalid_ohlc(client):
    rows = [{"timestamp": "2026-01-01T00:00:00Z", "open": 10, "high": 9, "low": 9.5, "close": 9.8, "volume": 1}]
    resp = client.post("/api/data/upload/manual", json={"asset": "X", "timeframe": "2m", "rows": rows})
    assert resp.status_code == 422


def test_unknown_timeframe_rejected(client):
    resp = client.post("/api/signal/generate", json={"asset": "X", "timeframe": "7m"})
    assert resp.status_code == 400


def test_paper_open_requires_confirm(client):
    resp = client.post("/api/paper/open", json={
        "asset": "EURUSD", "timeframe": "2m", "entry_price": 100.0, "confirm": False,
    })
    assert resp.status_code == 400


def test_paper_open_without_prior_signal_404s(client):
    resp = client.post("/api/paper/open", json={
        "asset": "NEVER/GENERATED", "timeframe": "2m", "entry_price": 100.0, "confirm": True,
    })
    assert resp.status_code == 404


def test_paper_full_flow(client):
    candles = deterministic_series(80, start_price=100.0, drift=0.3, amplitude=0.3)
    rows = candles_to_rows(candles)
    client.post("/api/data/upload/manual", json={"asset": "EURUSD", "timeframe": "2m", "rows": rows})
    gen = client.post("/api/signal/generate", json={"asset": "EURUSD", "timeframe": "2m"})
    direction = gen.json()["signal"]["direction"]

    open_resp = client.post("/api/paper/open", json={
        "asset": "EURUSD", "timeframe": "2m", "entry_price": 100.0, "confirm": True,
    })
    assert open_resp.status_code == 200
    body = open_resp.json()
    if direction == "NO TRADE":
        assert body.get("message") == "No trade opened."
    else:
        trade_id = body["id"]
        settle = client.post("/api/paper/settle", json={"trade_id": trade_id, "exit_price": 105.0})
        assert settle.status_code == 200

        account = client.get("/api/paper/account")
        assert account.status_code == 200
        assert account.json()["total_trades"] == 1


def test_paper_reset_requires_confirm(client):
    resp = client.post("/api/paper/reset", json={"confirm": False})
    assert resp.status_code == 400
    resp2 = client.post("/api/paper/reset", json={"confirm": True})
    assert resp2.status_code == 200


def test_risk_status_initially_active(client):
    resp = client.get("/api/risk/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACTIVE"


def test_backtest_without_data_404s(client):
    resp = client.post("/api/backtest/run", json={"asset": "NOPE", "timeframe": "2m"})
    assert resp.status_code == 404


def test_backtest_with_data_returns_disclaimer(client):
    candles = deterministic_series(80, start_price=100.0, drift=0.3, amplitude=0.3)
    rows = candles_to_rows(candles)
    client.post("/api/data/upload/manual", json={"asset": "EURUSD", "timeframe": "2m", "rows": rows})
    resp = client.post("/api/backtest/run", json={"asset": "EURUSD", "timeframe": "2m", "payout_ratio": 0.8})
    assert resp.status_code == 200
    body = resp.json()
    assert "historical simulation" in body["disclaimer"].lower()
    assert "stats" in body


def test_alert_config_rejects_invalid_confidence(client):
    resp = client.post("/api/alerts/config", json={"min_confidence_to_alert": "SUPER HIGH"})
    assert resp.status_code == 400


def test_alert_config_accepts_valid_confidence(client):
    resp = client.post("/api/alerts/config", json={"min_confidence_to_alert": "MEDIUM"})
    assert resp.status_code == 200
    assert resp.json()["min_confidence_to_alert"] == "MEDIUM"


def test_config_rejects_weights_not_summing_to_one(client):
    resp = client.post("/api/config", json={
        "timeframe": "2m",
        "weights": {
            "trend": 0.5, "momentum": 0.5, "volatility": 0.5, "price_action": 0.5,
            "support_resistance": 0.5, "indicator_confirmation": 0.5, "multi_timeframe": 0.5,
        },
    })
    assert resp.status_code == 400


def test_rate_limiter_blocks_after_threshold():
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check("client-a")
    with pytest.raises(Exception):
        limiter.check("client-a")
    # A different client key is independent.
    limiter.check("client-b")
