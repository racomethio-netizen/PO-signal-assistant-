"""Tests for the currency-pair/instrument registry upgrade.

Covers: isolated per-pair indicator calculations, standard-vs-OTC
distinction (never substituting one for the other), unsupported-pair
rejection, missing/stale data correctly blocking a signal, and that all
three existing timeframes keep working per pair.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.state as state_module
from api.main import app
from instruments import (
    AssetRegistry,
    DataSource,
    Instrument,
    InstrumentCategory,
    InstrumentType,
    check_binance_fetch_allowed,
    guess_instrument,
    otc_data_unavailable_reason,
)
from signals.engine import SignalEngine
from strategy.config import TIMEFRAME_CONFIGS
from tests.conftest import deterministic_series


@pytest.fixture
def client():
    state_module._state = None
    return TestClient(app)


def candles_to_rows(candles):
    return [
        {"timestamp": c.timestamp.isoformat(), "open": c.open, "high": c.high,
         "low": c.low, "close": c.close, "volume": c.volume}
        for c in candles
    ]


# --- Registry basics ---------------------------------------------------
def test_default_catalog_includes_all_required_majors_and_crosses():
    reg = AssetRegistry()
    majors = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]
    crosses = ["EURGBP", "GBPJPY", "AUDNZD", "CHFJPY", "CADJPY"]
    for symbol in majors + crosses:
        assert reg.is_known(symbol), f"{symbol} should be in the default catalog"
        inst = reg.get(symbol)
        assert inst.category == InstrumentCategory.FOREX
        assert inst.instrument_type == InstrumentType.STANDARD


def test_otc_variants_registered_for_majors():
    reg = AssetRegistry()
    for symbol in ["EURUSD_OTC", "GBPUSD_OTC", "USDJPY_OTC"]:
        inst = reg.get(symbol)
        assert inst is not None
        assert inst.instrument_type == InstrumentType.OTC
        assert "(OTC)" in inst.display_name


def test_standard_and_otc_are_distinct_entries():
    reg = AssetRegistry()
    standard = reg.get("EURUSD")
    otc = reg.get("EURUSD_OTC")
    assert standard.symbol != otc.symbol
    assert standard.instrument_type != otc.instrument_type


def test_unknown_instrument_lookup_returns_none():
    reg = AssetRegistry()
    assert reg.get("NOTAPAIR") is None
    assert not reg.is_known("NOTAPAIR")


def test_registry_search_and_filters():
    reg = AssetRegistry()
    gbp_matches = reg.list(query="gbp")
    assert all("GBP" in i.symbol for i in gbp_matches)
    otc_only = reg.list(instrument_type=InstrumentType.OTC)
    assert all(i.instrument_type == InstrumentType.OTC for i in otc_only)
    crypto_only = reg.list(category=InstrumentCategory.CRYPTO)
    assert all(i.category == InstrumentCategory.CRYPTO for i in crypto_only)


def test_custom_registration_is_the_user_provided_list_path():
    reg = AssetRegistry()
    custom = Instrument(
        symbol="XAUUSD", display_name="Gold/USD", base_currency="XAU", quote_currency="USD",
        category=InstrumentCategory.OTHER, instrument_type=InstrumentType.STANDARD,
        data_source=DataSource.MANUAL,
    )
    reg.register(custom)
    assert reg.is_known("XAUUSD")
    assert reg.get("XAUUSD").display_name == "Gold/USD"


def test_guess_instrument_is_generic_and_nonbreaking():
    inst = guess_instrument("WEIRD_SYMBOL")
    assert inst.symbol == "WEIRD_SYMBOL"
    assert inst.category == InstrumentCategory.OTHER


# --- Availability / freshness -------------------------------------------
def test_describe_unknown_symbol_returns_none():
    reg = AssetRegistry()
    assert reg.describe("NOTAPAIR", {}) is None


def test_describe_configured_with_no_data():
    reg = AssetRegistry()
    desc = reg.describe("EURUSD", {})
    assert desc["availability_status"] == "CONFIGURED"
    assert desc["last_successful_data_timestamp"] is None


def test_describe_available_with_fresh_data(uptrend_candles):
    from datetime import timedelta
    reg = AssetRegistry()
    now = uptrend_candles[-1].timestamp + timedelta(seconds=60)
    desc = reg.describe("EURUSD", {"2m": uptrend_candles}, now=now)
    assert desc["availability_status"] == "AVAILABLE"


def test_describe_stale_with_old_data(uptrend_candles):
    from datetime import timedelta
    reg = AssetRegistry()
    now = uptrend_candles[-1].timestamp + timedelta(hours=5)
    desc = reg.describe("EURUSD", {"2m": uptrend_candles}, now=now)
    assert desc["availability_status"] == "STALE"


# --- Binance/OTC source-consistency guards ----------------------------
def test_binance_fetch_rejected_for_forex():
    reg = AssetRegistry()
    check = check_binance_fetch_allowed(reg.get("EURUSD"))
    assert not check.ok


def test_binance_fetch_rejected_for_otc():
    reg = AssetRegistry()
    check = check_binance_fetch_allowed(reg.get("EURUSD_OTC"))
    assert not check.ok


def test_binance_fetch_allowed_for_standard_crypto():
    reg = AssetRegistry()
    check = check_binance_fetch_allowed(reg.get("BTCUSDT"))
    assert check.ok


def test_otc_unavailable_reason_names_the_instrument():
    reg = AssetRegistry()
    reason = otc_data_unavailable_reason(reg.get("GBPUSD_OTC"))
    assert "OTC DATA UNAVAILABLE" in reason
    assert "GBP/USD" in reason


# --- Isolation between pairs (engine-level) -----------------------------
def test_each_pair_has_independent_indicator_calculations():
    engine = SignalEngine()
    up = deterministic_series(90, start_price=100.0, drift=0.3, amplitude=0.3)
    down = deterministic_series(90, start_price=100.0, drift=-0.3, amplitude=0.3)

    sig_eurusd = engine.generate("EURUSD", TIMEFRAME_CONFIGS["2m"], up)
    sig_gbpusd = engine.generate("GBPUSD", TIMEFRAME_CONFIGS["2m"], down)

    assert sig_eurusd.direction.is_call
    assert sig_gbpusd.direction.is_put
    assert sig_eurusd.rsi["value"] != sig_gbpusd.rsi["value"]


def test_all_three_timeframes_work_per_pair():
    engine = SignalEngine()
    for tf_name, min_c in [("2m", 60), ("3m", 60), ("15m", 80)]:
        candles = deterministic_series(min_c + 10, start_price=100.0, drift=0.3, amplitude=0.3,
                                        step_seconds=TIMEFRAME_CONFIGS[tf_name].seconds)
        sig = engine.generate("EURUSD", TIMEFRAME_CONFIGS[tf_name], candles)
        assert sig.timeframe == tf_name
        assert sig.direction is not None


# --- API-level: full isolation + OTC + unsupported handling -------------
def test_api_standard_and_otc_pairs_store_isolated_data(client):
    up = deterministic_series(90, start_price=100.0, drift=0.3, amplitude=0.3)
    down = deterministic_series(90, start_price=100.0, drift=-0.3, amplitude=0.3)

    client.post("/api/data/upload/manual", json={
        "asset": "EURUSD", "timeframe": "2m", "rows": candles_to_rows(up),
    })
    client.post("/api/data/upload/manual", json={
        "asset": "EURUSD_OTC", "timeframe": "2m", "rows": candles_to_rows(down),
    })

    standard_signal = client.post("/api/signal/generate", json={"asset": "EURUSD", "timeframe": "2m"}).json()["signal"]
    otc_signal = client.post("/api/signal/generate", json={"asset": "EURUSD_OTC", "timeframe": "2m"}).json()["signal"]

    assert standard_signal["direction"].endswith("CALL")
    assert otc_signal["direction"].endswith("PUT")
    assert standard_signal["reference_price"] != otc_signal["reference_price"]


def test_api_otc_pair_with_no_data_returns_otc_specific_message(client):
    resp = client.post("/api/signal/generate", json={"asset": "GBPUSD_OTC", "timeframe": "2m"})
    assert resp.status_code == 200
    sig = resp.json()["signal"]
    assert sig["direction"] == "NO TRADE"
    assert "OTC DATA UNAVAILABLE" in sig["reasons_against"][0]


def test_api_standard_pair_with_no_data_returns_generic_message(client):
    resp = client.post("/api/signal/generate", json={"asset": "EURUSD", "timeframe": "2m"})
    sig = resp.json()["signal"]
    assert sig["reasons_against"][0] == "DATA UNAVAILABLE — NO SIGNAL"


def test_api_binance_fetch_rejects_otc_and_forex_symbols(client):
    resp = client.post("/api/data/fetch/binance", json={"asset": "EURUSD_OTC", "timeframe": "3m", "limit": 50})
    assert resp.status_code == 400
    resp2 = client.post("/api/data/fetch/binance", json={"asset": "EURUSD", "timeframe": "3m", "limit": 50})
    assert resp2.status_code == 400


def test_api_list_instruments_filters(client):
    resp = client.get("/api/instruments?instrument_type=OTC")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 7
    assert all(i["instrument_type"] == "OTC" for i in body)


def test_api_get_unknown_instrument_404s(client):
    resp = client.get("/api/instruments/NOTREAL")
    assert resp.status_code == 404


def test_api_register_custom_instrument(client):
    resp = client.post("/api/instruments/register", json={
        "symbol": "XPTUSD", "display_name": "Platinum/USD", "base_currency": "XPT",
        "quote_currency": "USD", "category": "OTHER", "instrument_type": "STANDARD",
        "data_source": "manual",
    })
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "XPTUSD"
    assert client.get("/api/instruments/XPTUSD").status_code == 200


def test_api_register_rejects_invalid_category(client):
    resp = client.post("/api/instruments/register", json={
        "symbol": "X", "display_name": "X", "base_currency": "X", "quote_currency": "X",
        "category": "NOT_A_CATEGORY", "instrument_type": "STANDARD", "data_source": "manual",
    })
    assert resp.status_code == 400


def test_api_uploading_unknown_symbol_auto_registers_it(client):
    candles = deterministic_series(90, start_price=50.0, drift=0.1, amplitude=0.2)
    client.post("/api/data/upload/manual", json={
        "asset": "CUSTOMPAIR", "timeframe": "2m", "rows": candles_to_rows(candles),
    })
    resp = client.get("/api/instruments/CUSTOMPAIR")
    assert resp.status_code == 200
    body = resp.json()
    # deterministic_series anchors to a fixed past date, so relative to
    # real wall-clock time this reads STALE rather than AVAILABLE — the
    # point of this test is that it's no longer CONFIGURED/UNSUPPORTED,
    # i.e. the upload was recognized and the instrument auto-registered.
    assert body["availability_status"] in ("AVAILABLE", "STALE")
    assert body["timeframes_loaded"] == ["2m"]


def test_api_binance_fetch_success_auto_registers_as_crypto(client):
    from unittest.mock import patch
    candles = deterministic_series(60, start_price=50000.0, drift=5.0, amplitude=10.0, step_seconds=180)
    with patch("api.main._binance_adapter.fetch_candles", return_value=candles):
        client.post("/api/data/fetch/binance", json={"asset": "ADAUSDT", "timeframe": "3m", "limit": 60})
    resp = client.get("/api/instruments/ADAUSDT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "CRYPTO"
    assert body["data_source"] == "binance"


# --- Pair comparison ------------------------------------------------------
def test_api_compare_with_no_data_returns_no_suitable_setup(client):
    resp = client.post("/api/instruments/compare", json={"timeframe": "2m"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "NO SUITABLE SETUP — WAIT"
    assert body["candidates"] == []


def test_api_compare_ranks_candidates_with_sample_size_and_not_just_wins(client):
    up = deterministic_series(90, start_price=100.0, drift=0.3, amplitude=0.3)
    down = deterministic_series(90, start_price=100.0, drift=-0.3, amplitude=0.3)
    client.post("/api/data/upload/manual", json={"asset": "EURUSD", "timeframe": "2m", "rows": candles_to_rows(up)})
    client.post("/api/data/upload/manual", json={"asset": "GBPUSD", "timeframe": "2m", "rows": candles_to_rows(down)})

    resp = client.post("/api/instruments/compare", json={"timeframe": "2m"})
    body = resp.json()
    assert body["sample_size"] == 2
    assert "never" in body["note"].lower()  # names that it's not win-count-only
    for c in body["candidates"]:
        assert "composite_rank_score" in c
        assert "data_quality_score" in c
    # sorted descending by composite rank
    scores = [c["composite_rank_score"] for c in body["candidates"]]
    assert scores == sorted(scores, reverse=True)


def test_api_compare_unknown_timeframe_rejected(client):
    resp = client.post("/api/instruments/compare", json={"timeframe": "7m"})
    assert resp.status_code == 400


# --- Signal history enrichment -------------------------------------------
def test_signal_history_includes_instrument_metadata(client):
    candles = deterministic_series(90, start_price=100.0, drift=0.3, amplitude=0.3)
    client.post("/api/data/upload/manual", json={
        "asset": "EURUSD_OTC", "timeframe": "2m", "rows": candles_to_rows(candles),
    })
    client.post("/api/signal/generate", json={"asset": "EURUSD_OTC", "timeframe": "2m"})

    history = client.get("/api/signal/history?limit=5").json()
    assert history[0]["asset"] == "EURUSD_OTC"
    assert history[0]["instrument_type"] == "OTC"
    assert history[0]["data_source"] == "manual"
    assert history[0]["reference_price"] is not None
