"""Optional Telegram alert sender.

Sends a read-only notification describing a signal that was already
computed. This module can never place, modify, or cancel a trade — it
only ever issues an HTTP POST to the Telegram Bot API's sendMessage
endpoint. All calls are rate-limited and time out quickly so a Telegram
outage can never block signal generation.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx

from signals.models import Signal

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 10.0


def format_signal_message(signal: Signal) -> str:
    s = signal.to_dict()
    lines = [
        "📊 SIGNAL ALERT",
        "",
        f"{s['asset']}",
        f"{s['timeframe'].upper()}",
        "",
        f"{s['direction']}",
        "",
        f"Confidence: {s['confidence']}",
        f"Score: {round(s['score'])}/100",
        "",
        f"MACD: {'Bullish' if (s['macd'].get('histogram') or 0) > 0 else 'Bearish'}",
    ]
    rsi_val = s["rsi"].get("value")
    if rsi_val is not None:
        lines.append(f"RSI: {rsi_val:.0f}")
    lines.append(f"Trend: {s['trend'].get('detail', 'n/a')}")
    sar_trend = s["sar"].get("trend")
    if sar_trend:
        lines.append(f"SAR: {'Bullish' if sar_trend == 'up' else 'Bearish'}")

    nearby = [lvl for lvl in s["support_resistance"].get("levels", []) if lvl["distance_pct"] < 0.5]
    if nearby:
        lvl = nearby[0]
        lines.append(f"\n⚠ {lvl['kind'].title()} nearby ({lvl['distance_pct']:.2f}% away)")

    lines.append("\nAnalysis only — no trade executed.")
    return "\n".join(lines)


class TelegramAlerter:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        min_interval_seconds: Optional[float] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.min_interval_seconds = min_interval_seconds or float(
            os.environ.get("TELEGRAM_MIN_INTERVAL_SECONDS", "60")
        )
        self.timeout_seconds = timeout_seconds
        self._last_sent_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _rate_limited(self) -> bool:
        return (time.monotonic() - self._last_sent_at) < self.min_interval_seconds

    def send_signal_alert(self, signal: Signal, force: bool = False) -> bool:
        """Best-effort send; returns True only on a confirmed successful
        delivery. Never raises — any failure is swallowed so alerting can
        never interrupt signal generation."""
        if not self.is_configured:
            return False
        if not force and self._rate_limited():
            return False

        message = format_signal_message(signal)
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        try:
            resp = httpx.post(
                url,
                json={"chat_id": self.chat_id, "text": message},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                return False
        except Exception:
            return False

        self._last_sent_at = time.monotonic()
        return True
