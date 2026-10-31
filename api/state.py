"""In-memory application state for the FastAPI app.

This is a single-process analysis assistant, not a multi-tenant trading
backend — state lives in memory for the life of the process. Candle data,
signal history, and the paper-trading session all reset on restart. This
is a documented limitation (see README), not an oversight: adding a
persistence layer is straightforward future work and does not change any
of the analysis/risk logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

from data.models import Candle
from instruments import AssetRegistry, guess_instrument
from instruments.providers import MarketDataProvider, get_configured_provider
from llm.anthropic_provider import AnthropicProvider
from alerts.dispatcher import AlertConfig, AlertManager
from alerts.telegram import TelegramAlerter
from paper.simulator import PaperAccount
from risk.manager import RiskConfig, RiskManager
from signals.engine import SignalEngine
from signals.models import Confidence, Signal
from strategy.config import (
    DEFAULT_INDICATOR_CONFIG,
    DEFAULT_MIN_SIGNAL_SCORE_THRESHOLD,
    TIMEFRAME_CONFIGS,
    TimeframeConfig,
)

MAX_SIGNAL_HISTORY = 500


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val else default


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val else default


@dataclass
class AppState:
    candle_store: Dict[str, Dict[str, List[Candle]]] = field(default_factory=dict)
    asset_registry: AssetRegistry = field(default_factory=AssetRegistry)
    _market_data_provider: Optional[MarketDataProvider] = field(default=None, init=False, repr=False)
    timeframe_configs: Dict[str, TimeframeConfig] = field(
        default_factory=lambda: dict(TIMEFRAME_CONFIGS)
    )
    min_score_threshold: float = field(
        default_factory=lambda: _env_float(
            "SIGNAL_MIN_SCORE_THRESHOLD", DEFAULT_MIN_SIGNAL_SCORE_THRESHOLD
        )
    )
    signal_history: List[Signal] = field(default_factory=list)

    llm_provider: AnthropicProvider = field(default_factory=AnthropicProvider)

    risk_manager: RiskManager = field(
        default_factory=lambda: RiskManager(
            RiskConfig(
                starting_balance=_env_float("PAPER_STARTING_BALANCE", 1000.0),
                max_stake_pct=_env_float("RISK_MAX_STAKE_PCT", 0.01),
                max_daily_loss_pct=_env_float("RISK_MAX_DAILY_LOSS_PCT", 0.03),
                max_consecutive_losses=_env_int("RISK_MAX_CONSECUTIVE_LOSSES", 3),
                cooldown_trades=_env_int("RISK_COOLDOWN_TRADES", 5),
                max_trades_per_day=_env_int("PAPER_MAX_TRADES_PER_DAY", 20),
            )
        )
    )
    paper_account: Optional[PaperAccount] = None

    telegram: TelegramAlerter = field(default_factory=TelegramAlerter)
    alert_manager: AlertManager = field(
        default_factory=lambda: AlertManager(
            AlertConfig(telegram_enabled=_env_bool("TELEGRAM_ENABLED", False))
        )
    )

    def __post_init__(self) -> None:
        if self.paper_account is None:
            self.paper_account = PaperAccount(self.risk_manager)
        self.alert_manager.telegram = self.telegram

    def signal_engine(self) -> SignalEngine:
        return SignalEngine(
            indicator_config=DEFAULT_INDICATOR_CONFIG,
            min_score_threshold=self.min_score_threshold,
            llm_provider=self.llm_provider,
        )

    def store_candles(self, asset: str, timeframe: str, candles: List[Candle]) -> None:
        self.candle_store.setdefault(asset, {})[timeframe] = candles
        if not self.asset_registry.is_known(asset):
            self.asset_registry.register(guess_instrument(asset))

    def instrument_description(self, symbol: str) -> Optional[Dict]:
        return self.asset_registry.describe(symbol, self.candle_store.get(symbol, {}))

    def market_data_provider(self) -> Optional[MarketDataProvider]:
        """Lazily built and cached so a provider's connection-status
        tracking (STEP 8) persists across requests. Raises
        ProviderConfigError (uncaught here) if MARKET_DATA_PROVIDER is set
        to something misconfigured — callers decide how to surface that."""
        if self._market_data_provider is None:
            self._market_data_provider = get_configured_provider()
        return self._market_data_provider

    def get_candles(self, asset: str, timeframe: str) -> Optional[List[Candle]]:
        return self.candle_store.get(asset, {}).get(timeframe)

    def get_confirmation_candles(self, asset: str, exclude_timeframe: str) -> Dict[str, List[Candle]]:
        assets = self.candle_store.get(asset, {})
        return {tf: candles for tf, candles in assets.items() if tf != exclude_timeframe}

    def record_signal(self, signal: Signal) -> None:
        self.signal_history.append(signal)
        if len(self.signal_history) > MAX_SIGNAL_HISTORY:
            self.signal_history = self.signal_history[-MAX_SIGNAL_HISTORY:]

    def latest_signal(self, asset: str, timeframe: str) -> Optional[Signal]:
        for s in reversed(self.signal_history):
            if s.asset == asset and s.timeframe == timeframe:
                return s
        return None

    def set_weights(self, timeframe: str, weights) -> None:
        cfg = self.timeframe_configs[timeframe]
        self.timeframe_configs[timeframe] = replace(cfg, weights=weights)

    def rebuild_risk(self, risk_config: RiskConfig, payout_ratio: float) -> None:
        self.risk_manager = RiskManager(risk_config)
        self.paper_account = PaperAccount(self.risk_manager, payout_ratio=payout_ratio)


_state: Optional[AppState] = None


def get_state() -> AppState:
    global _state
    if _state is None:
        _state = AppState()
    return _state
