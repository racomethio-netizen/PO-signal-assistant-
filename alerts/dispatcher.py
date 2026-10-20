"""Coordinates whether/how to alert on a newly generated signal.

Decides eligibility (confidence threshold + de-duplication) and dispatches
the optional Telegram alert. Browser and sound alerts are inherently
client-side (the browser Notification API and an <audio> element); this
module just tells the frontend whether to fire them, via the
``should_alert_browser``/``should_alert_sound`` flags in its result. No
alert of any kind ever triggers a trade.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from signals.models import Confidence, Direction, Signal

from .telegram import TelegramAlerter

CONFIDENCE_RANK = {
    Confidence.VERY_LOW: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
    Confidence.VERY_HIGH: 4,
}


@dataclass
class AlertConfig:
    min_confidence_to_alert: Confidence = Confidence.HIGH
    browser_enabled: bool = True
    sound_enabled: bool = True
    telegram_enabled: bool = False
    dedupe_window_seconds: float = 120.0


@dataclass
class AlertDecision:
    should_alert: bool
    browser: bool
    sound: bool
    telegram_sent: bool
    reason: str

    def to_dict(self) -> Dict:
        return {
            "should_alert": self.should_alert,
            "browser": self.browser,
            "sound": self.sound,
            "telegram_sent": self.telegram_sent,
            "reason": self.reason,
        }


class AlertManager:
    def __init__(self, config: AlertConfig, telegram: Optional[TelegramAlerter] = None) -> None:
        self.config = config
        self.telegram = telegram
        self._last_alert_key: Optional[Tuple] = None
        self._last_alert_time: float = 0.0

    def _dedupe_key(self, signal: Signal) -> Tuple:
        return (signal.asset, signal.timeframe, signal.direction.value, round(signal.score))

    def _is_duplicate(self, signal: Signal) -> bool:
        key = self._dedupe_key(signal)
        if key == self._last_alert_key and (
            time.monotonic() - self._last_alert_time < self.config.dedupe_window_seconds
        ):
            return True
        return False

    def process(self, signal: Signal) -> AlertDecision:
        if signal.direction == Direction.NO_TRADE:
            return AlertDecision(False, False, False, False, "NO TRADE — nothing to alert on.")

        if CONFIDENCE_RANK[signal.confidence] < CONFIDENCE_RANK[self.config.min_confidence_to_alert]:
            return AlertDecision(
                False, False, False, False,
                f"Confidence {signal.confidence.value} below alert threshold "
                f"{self.config.min_confidence_to_alert.value}.",
            )

        if self._is_duplicate(signal):
            return AlertDecision(False, False, False, False, "Duplicate of the most recent alert; suppressed.")

        telegram_sent = False
        if self.config.telegram_enabled and self.telegram is not None:
            telegram_sent = self.telegram.send_signal_alert(signal)

        self._last_alert_key = self._dedupe_key(signal)
        self._last_alert_time = time.monotonic()

        return AlertDecision(
            should_alert=True,
            browser=self.config.browser_enabled,
            sound=self.config.sound_enabled,
            telegram_sent=telegram_sent,
            reason="Signal meets the configured alert confidence threshold.",
        )
