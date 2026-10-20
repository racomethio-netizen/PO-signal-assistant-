"""FastAPI backend for the Pocket Option Signal Assistant.

ANALYSIS AND SIGNAL ASSISTANT ONLY. No endpoint in this file places,
modifies, or cancels a trade on Pocket Option or any other broker. The
only "trade" concept here is a PAPER (simulated) trade recorded for
bookkeeping purposes, and even that requires explicit ``confirm: true``
from the caller.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

from data.loaders import DataFormatError, load_csv, load_json, load_manual
from data.validation import validate_candles
from data.binance_adapter import BinanceAdapter, BinanceDataFormatError
from backtest.engine import BacktestEngine
from backtest.walkforward import run_walk_forward
from paper.simulator import PaperAccount
from risk.manager import RiskConfig
from signals.models import Confidence, Direction
from strategy.config import TIMEFRAME_LABEL_SECONDS

from .rate_limit import RateLimiter, client_key
from .schemas import (
    AlertConfigRequest,
    BacktestRequest,
    BinanceFetchRequest,
    JSONCandlesRequest,
    ManualCandlesRequest,
    PaperOpenRequest,
    PaperResetRequest,
    PaperSettleRequest,
    RiskConfigRequest,
    SignalGenerateRequest,
    StrategyConfigRequest,
)
from .state import AppState, get_state
from strategy.config import ScoreWeights

app = FastAPI(
    title="Pocket Option Signal Assistant API",
    description=(
        "Analysis and signal assistant for 2m/3m/15m technical setups. "
        "PAPER/DEMO mode only. No automated trading."
    ),
    version="1.0.0",
)

_cors_origins = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:8000"
).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

signal_limiter = RateLimiter(max_requests=30, window_seconds=60)
backtest_limiter = RateLimiter(max_requests=10, window_seconds=60)
binance_limiter = RateLimiter(max_requests=20, window_seconds=60)

_binance_adapter = BinanceAdapter()


def _timeframe_config(state: AppState, timeframe: str):
    cfg = state.timeframe_configs.get(timeframe)
    if cfg is None:
        raise HTTPException(400, f"Unknown timeframe '{timeframe}'. Use one of: "
                                  f"{list(state.timeframe_configs.keys())}")
    return cfg


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": os.environ.get("APP_MODE", "PAPER")}


# --- Data ingestion -----------------------------------------------------
@app.post("/api/data/upload/csv")
async def upload_csv(
    asset: str = Form(...),
    timeframe: str = Form(...),
    file: UploadFile = File(...),
    state: AppState = Depends(get_state),
):
    if timeframe not in TIMEFRAME_LABEL_SECONDS:
        raise HTTPException(400, f"Unknown timeframe '{timeframe}'.")
    raw = await file.read()
    try:
        candles = load_csv(raw)
    except DataFormatError as exc:
        raise HTTPException(422, str(exc))
    return _validate_and_store(state, asset, timeframe, candles)


@app.post("/api/data/upload/json")
def upload_json(body: JSONCandlesRequest, state: AppState = Depends(get_state)):
    if body.timeframe not in TIMEFRAME_LABEL_SECONDS:
        raise HTTPException(400, f"Unknown timeframe '{body.timeframe}'.")
    try:
        candles = load_json(body.candles)
    except DataFormatError as exc:
        raise HTTPException(422, str(exc))
    return _validate_and_store(state, body.asset, body.timeframe, candles)


@app.post("/api/data/upload/manual")
def upload_manual(body: ManualCandlesRequest, state: AppState = Depends(get_state)):
    if body.timeframe not in TIMEFRAME_LABEL_SECONDS:
        raise HTTPException(400, f"Unknown timeframe '{body.timeframe}'.")
    try:
        candles = load_manual([row.model_dump() for row in body.rows])
    except DataFormatError as exc:
        raise HTTPException(422, str(exc))
    return _validate_and_store(state, body.asset, body.timeframe, candles)


@app.post("/api/data/fetch/binance")
def fetch_binance(
    request: Request,
    body: BinanceFetchRequest,
    state: AppState = Depends(get_state),
):
    """Fetch real historical OHLCV candles for a crypto pair from Binance's
    public (no-login, read-only) klines endpoint. Never touches Pocket
    Option or any broker; cannot place an order."""
    binance_limiter.check(client_key(request))
    if body.timeframe not in TIMEFRAME_LABEL_SECONDS:
        raise HTTPException(400, f"Unknown timeframe '{body.timeframe}'.")
    seconds = TIMEFRAME_LABEL_SECONDS[body.timeframe]
    try:
        candles = _binance_adapter.fetch_candles(body.asset, seconds, body.limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except BinanceDataFormatError as exc:
        raise HTTPException(502, f"Binance fetch failed: {exc}")
    if not candles:
        raise HTTPException(502, f"Binance returned no candles for '{body.asset}' at {body.timeframe}.")
    return _validate_and_store(state, body.asset, body.timeframe, candles)


def _validate_and_store(state: AppState, asset: str, timeframe: str, candles):
    seconds = TIMEFRAME_LABEL_SECONDS[timeframe]
    validation = validate_candles(candles, seconds, min_candles=3, max_stale_seconds=None)
    if not validation.ok:
        raise HTTPException(422, detail={
            "message": "Candle data failed validation.",
            "errors": [e.message for e in validation.errors],
            "warnings": [w.message for w in validation.warnings],
        })
    state.store_candles(asset, timeframe, candles)
    return {
        "asset": asset,
        "timeframe": timeframe,
        "candle_count": len(candles),
        "first_timestamp": candles[0].timestamp.isoformat() if candles else None,
        "last_timestamp": candles[-1].timestamp.isoformat() if candles else None,
        "warnings": [w.message for w in validation.warnings],
    }


@app.get("/api/data/status")
def data_status(asset: str, timeframe: str, state: AppState = Depends(get_state)):
    candles = state.get_candles(asset, timeframe)
    if not candles:
        return {"asset": asset, "timeframe": timeframe, "candle_count": 0}
    return {
        "asset": asset,
        "timeframe": timeframe,
        "candle_count": len(candles),
        "first_timestamp": candles[0].timestamp.isoformat(),
        "last_timestamp": candles[-1].timestamp.isoformat(),
    }


@app.get("/api/data/assets")
def list_assets(state: AppState = Depends(get_state)):
    return {
        asset: list(timeframes.keys())
        for asset, timeframes in state.candle_store.items()
    }


# --- Signals --------------------------------------------------------------
@app.post("/api/signal/generate")
def generate_signal(
    request: Request,
    body: SignalGenerateRequest,
    state: AppState = Depends(get_state),
):
    signal_limiter.check(client_key(request))
    candles = state.get_candles(body.asset, body.timeframe)
    tf_cfg = _timeframe_config(state, body.timeframe)
    if not candles:
        from signals.models import no_trade_signal
        from datetime import datetime, timezone
        sig = no_trade_signal(
            body.asset, body.timeframe, datetime.now(timezone.utc),
            "DATA UNAVAILABLE — NO SIGNAL",
        )
    else:
        confirmation = state.get_confirmation_candles(body.asset, body.timeframe)
        engine = state.signal_engine()
        sig = engine.generate(
            asset=body.asset,
            timeframe_config=tf_cfg,
            candles=candles,
            confirmation_candles_by_label=confirmation,
            explain_with_llm=body.explain_with_llm,
        )
    state.record_signal(sig)
    decision = state.alert_manager.process(sig)
    return {"signal": sig.to_dict(), "alert": decision.to_dict()}


@app.get("/api/signal/multi-timeframe")
def multi_timeframe(asset: str, state: AppState = Depends(get_state)):
    results = {}
    for tf_name, tf_cfg in state.timeframe_configs.items():
        candles = state.get_candles(asset, tf_name)
        if not candles:
            results[tf_name] = {"available": False}
            continue
        confirmation = state.get_confirmation_candles(asset, tf_name)
        engine = state.signal_engine()
        sig = engine.generate(asset, tf_cfg, candles, confirmation)
        state.record_signal(sig)
        results[tf_name] = {
            "available": True,
            "trend": sig.trend,
            "momentum": sig.momentum,
            "signal": sig.direction.value,
            "confidence": sig.confidence.value,
            "score": round(sig.score, 2),
        }
    return results


@app.get("/api/signal/history")
def signal_history(limit: int = 50, state: AppState = Depends(get_state)):
    items = state.signal_history[-limit:]
    out = []
    for s in reversed(items):
        matching_trade = next(
            (t for t in reversed(state.paper_account.history)
             if t.asset == s.asset and t.timeframe == s.timeframe
             and abs((t.timestamp - s.generated_at).total_seconds()) < 5),
            None,
        )
        out.append({
            "timestamp": s.generated_at.isoformat(),
            "asset": s.asset,
            "timeframe": s.timeframe,
            "signal": s.direction.value,
            "confidence": s.confidence.value,
            "score": round(s.score, 2),
            "result": matching_trade.status if matching_trade else "N/A",
        })
    return out


# --- Strategy configuration ------------------------------------------------
@app.get("/api/config")
def get_config(state: AppState = Depends(get_state)):
    return {
        "min_score_threshold": state.min_score_threshold,
        "timeframes": {
            name: {
                "seconds": cfg.seconds,
                "min_candles": cfg.min_candles,
                "weights": cfg.weights.as_dict(),
                "expiry_bars": cfg.expiry_bars,
            }
            for name, cfg in state.timeframe_configs.items()
        },
    }


@app.post("/api/config")
def update_config(body: StrategyConfigRequest, state: AppState = Depends(get_state)):
    tf_cfg = _timeframe_config(state, body.timeframe)
    if body.weights is not None:
        try:
            weights = ScoreWeights(**body.weights.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        state.set_weights(body.timeframe, weights)
    if body.min_score_threshold is not None:
        if not (0 <= body.min_score_threshold <= 100):
            raise HTTPException(400, "min_score_threshold must be between 0 and 100.")
        state.min_score_threshold = body.min_score_threshold
    return get_config(state)


# --- Backtesting ------------------------------------------------------------
@app.post("/api/backtest/run")
def run_backtest(request: Request, body: BacktestRequest, state: AppState = Depends(get_state)):
    backtest_limiter.check(client_key(request))
    candles = state.get_candles(body.asset, body.timeframe)
    if not candles:
        raise HTTPException(404, "No historical candles uploaded for this asset/timeframe.")
    tf_cfg = _timeframe_config(state, body.timeframe)
    engine = state.signal_engine()
    confirmation = state.get_confirmation_candles(body.asset, body.timeframe)

    if body.walk_forward:
        report = run_walk_forward(
            engine, body.asset, tf_cfg, candles,
            n_windows=body.n_windows, train_ratio=body.train_ratio,
            confirmation_full_history=confirmation,
        )
        result = report.to_dict()
    else:
        backtester = BacktestEngine(engine)
        report = backtester.run(
            body.asset, tf_cfg, candles,
            confirmation_full_history=confirmation,
            payout_ratio=body.payout_ratio,
            min_score_threshold=body.min_score_threshold,
        )
        result = report.to_dict()

    if body.explain_with_llm and state.llm_provider is not None:
        try:
            result["ai_review"] = state.llm_provider.review_backtest(result)
        except Exception:
            result["ai_review"] = "AI EXPLANATION UNAVAILABLE"
    return result


# --- Paper trading -----------------------------------------------------
@app.post("/api/paper/open")
def paper_open(body: PaperOpenRequest, state: AppState = Depends(get_state)):
    if not body.confirm:
        raise HTTPException(400, "Explicit confirm=true is required to open a paper trade.")
    sig = state.latest_signal(body.asset, body.timeframe)
    if sig is None:
        raise HTTPException(404, "No signal has been generated yet for this asset/timeframe.")
    if sig.direction == Direction.NO_TRADE:
        raise HTTPException(400, "The latest signal is NO TRADE; there is nothing to open.")
    tf_cfg = _timeframe_config(state, body.timeframe)
    trade = state.paper_account.open_trade(
        sig, body.entry_price, expiry_seconds=tf_cfg.seconds * tf_cfg.expiry_bars,
    )
    return trade.to_dict() if trade else {"message": "No trade opened."}


@app.post("/api/paper/settle")
def paper_settle(body: PaperSettleRequest, state: AppState = Depends(get_state)):
    try:
        trade = state.paper_account.settle_trade(body.trade_id, body.exit_price)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return trade.to_dict()


@app.get("/api/paper/account")
def paper_account(state: AppState = Depends(get_state)):
    return state.paper_account.summary()


@app.get("/api/paper/history")
def paper_history(state: AppState = Depends(get_state)):
    return [t.to_dict() for t in state.paper_account.history]


@app.post("/api/paper/reset")
def paper_reset(body: PaperResetRequest, state: AppState = Depends(get_state)):
    if not body.confirm:
        raise HTTPException(400, "Explicit confirm=true is required to reset the paper session.")
    state.paper_account.reset()
    return state.paper_account.summary()


@app.post("/api/paper/config")
def paper_config(body: RiskConfigRequest, state: AppState = Depends(get_state)):
    risk_config = RiskConfig(
        starting_balance=body.starting_balance,
        stake_mode=body.stake_mode,
        fixed_stake_amount=body.fixed_stake_amount,
        max_stake_pct=body.max_stake_pct,
        max_daily_loss_pct=body.max_daily_loss_pct,
        max_consecutive_losses=body.max_consecutive_losses,
        cooldown_trades=body.cooldown_trades,
        max_trades_per_day=body.max_trades_per_day,
    )
    state.rebuild_risk(risk_config, body.payout_ratio)
    return state.paper_account.summary()


@app.get("/api/risk/status")
def risk_status(state: AppState = Depends(get_state)):
    return state.risk_manager.to_dict()


# --- Alerts ------------------------------------------------------------
@app.get("/api/alerts/config")
def get_alert_config(state: AppState = Depends(get_state)):
    cfg = state.alert_manager.config
    return {
        "min_confidence_to_alert": cfg.min_confidence_to_alert.value,
        "browser_enabled": cfg.browser_enabled,
        "sound_enabled": cfg.sound_enabled,
        "telegram_enabled": cfg.telegram_enabled,
        "telegram_configured": state.telegram.is_configured,
    }


@app.post("/api/alerts/config")
def set_alert_config(body: AlertConfigRequest, state: AppState = Depends(get_state)):
    try:
        min_conf = Confidence(body.min_confidence_to_alert)
    except ValueError:
        raise HTTPException(400, f"Invalid confidence level '{body.min_confidence_to_alert}'.")
    state.alert_manager.config.min_confidence_to_alert = min_conf
    state.alert_manager.config.browser_enabled = body.browser_enabled
    state.alert_manager.config.sound_enabled = body.sound_enabled
    state.alert_manager.config.telegram_enabled = body.telegram_enabled
    return get_alert_config(state)


# --- Static frontend -------------------------------------------------------
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
