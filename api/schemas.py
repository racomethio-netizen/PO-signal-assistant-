"""Pydantic request/response models for the FastAPI app."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class OHLCVRow(BaseModel):
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class ManualCandlesRequest(BaseModel):
    asset: str
    timeframe: str
    rows: List[OHLCVRow]


class JSONCandlesRequest(BaseModel):
    asset: str
    timeframe: str
    candles: List[Dict[str, Any]]


KEEP_UPLOADS_DESCRIPTION = "If true, candles the user uploaded for this asset/timeframe are kept, not replaced."


class BinanceFetchRequest(BaseModel):
    asset: str = Field(..., description="Binance symbol, e.g. 'BTCUSDT' or 'BTC/USDT'.")
    timeframe: str
    limit: int = Field(200, gt=0, le=1500, description="Number of closed candles to fetch.")
    keep_uploads: bool = Field(False, description=KEEP_UPLOADS_DESCRIPTION)


class MarketDataFetchRequest(BaseModel):
    asset: str = Field(..., description="Instrument symbol as known to the registry, e.g. 'EURUSD'.")
    timeframe: str
    limit: int = Field(200, gt=0, le=1500, description="Number of closed candles to fetch.")
    keep_uploads: bool = Field(False, description=KEEP_UPLOADS_DESCRIPTION)


class SignalGenerateRequest(BaseModel):
    asset: str
    timeframe: str
    explain_with_llm: bool = False


class RiskConfigRequest(BaseModel):
    starting_balance: float = 1000.0
    stake_mode: str = "percent"
    fixed_stake_amount: float = 10.0
    max_stake_pct: float = 0.01
    max_daily_loss_pct: float = 0.03
    max_consecutive_losses: int = 3
    cooldown_trades: int = 5
    max_trades_per_day: int = 20
    payout_ratio: float = 0.8


class WeightsRequest(BaseModel):
    trend: float
    momentum: float
    volatility: float
    price_action: float
    support_resistance: float
    multi_timeframe: float
    candle_quality: float
    market_regime: float


class StrategyConfigRequest(BaseModel):
    timeframe: str
    weights: Optional[WeightsRequest] = None
    min_score_threshold: Optional[float] = None


class BacktestRequest(BaseModel):
    asset: str
    timeframe: str
    payout_ratio: float = 0.8
    min_score_threshold: Optional[float] = None
    walk_forward: bool = False
    n_windows: int = 4
    train_ratio: float = 0.5
    explain_with_llm: bool = False


class PaperOpenRequest(BaseModel):
    asset: str
    timeframe: str
    entry_price: float
    confirm: bool = Field(..., description="Explicit user confirmation required to open a paper trade.")


class PaperSettleRequest(BaseModel):
    trade_id: str
    exit_price: float


class PaperResetRequest(BaseModel):
    confirm: bool = Field(..., description="Explicit user confirmation required to reset the paper session.")


class AlertConfigRequest(BaseModel):
    min_confidence_to_alert: str = "HIGH"
    browser_enabled: bool = True
    sound_enabled: bool = True
    telegram_enabled: bool = False


class InstrumentRegisterRequest(BaseModel):
    symbol: str = Field(..., description="Canonical instrument id, e.g. 'EURUSD', 'EURUSD_OTC', 'BTCUSDT'.")
    display_name: str
    base_currency: str
    quote_currency: str
    category: str = Field("FOREX", description="FOREX | CRYPTO | OTHER")
    instrument_type: str = Field("STANDARD", description="STANDARD | OTC")
    data_source: str = Field("manual", description="binance | manual | unconfigured")
    supported_timeframes: List[str] = ["2m", "3m", "15m"]


class InstrumentCompareRequest(BaseModel):
    timeframe: str
    symbols: Optional[List[str]] = Field(
        None, description="Limit comparison to these symbols; omit to compare every instrument with loaded data."
    )
