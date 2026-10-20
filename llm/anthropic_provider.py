"""Anthropic (Claude) implementation of LLMProvider.

Used only to narrate deterministic analysis already computed elsewhere.
Every prompt explicitly instructs the model not to invent numbers, not to
promise an outcome, and to call out conflicting evidence — matching the
house style enforced throughout this project ("technical alignment",
"probabilistic analysis", never "guaranteed").
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .provider import AI_UNAVAILABLE_MESSAGE, LLMProvider

SYSTEM_PROMPT = (
    "You are a technical-analysis narrator for a Pocket Option signal "
    "assistant. You are given ALREADY-COMPUTED deterministic indicator "
    "values and a scoring breakdown. Your only job is to explain them in "
    "plain, concise language for a trader.\n\n"
    "Rules you must follow:\n"
    "- Never invent or alter any number; use only the values given.\n"
    "- Never claim a guaranteed, certain, or 100% outcome.\n"
    "- Explicitly call out any conflicting or weak evidence.\n"
    "- Use phrases like 'technical alignment' and 'probabilistic analysis', "
    "never 'guaranteed win' or 'can't lose'.\n"
    "- Keep it under 120 words.\n"
    "- End with one short sentence noting this is analysis, not financial advice."
)


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
        self.timeout_seconds = timeout_seconds or float(
            os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "20")
        )
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            return None
        try:
            import anthropic
        except ImportError:
            return None
        self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_seconds)
        return self._client

    def _complete(self, user_content: str) -> str:
        client = self._get_client()
        if client is None:
            return AI_UNAVAILABLE_MESSAGE
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            text_parts = [
                block.text for block in response.content if getattr(block, "type", None) == "text"
            ]
            text = "\n".join(text_parts).strip()
            if not text:
                return AI_UNAVAILABLE_MESSAGE
            return text
        except Exception:
            # Network error, timeout, API error, malformed response, etc.
            # Deterministic analysis must continue regardless.
            return AI_UNAVAILABLE_MESSAGE

    def explain_signal(self, signal) -> str:
        try:
            payload = signal.to_dict()
        except Exception:
            return AI_UNAVAILABLE_MESSAGE

        prompt = (
            f"Asset: {payload['asset']}\n"
            f"Timeframe: {payload['timeframe']}\n"
            f"Direction: {payload['direction']}\n"
            f"Confidence: {payload['confidence']}\n"
            f"Score: {payload['score']}/100\n"
            f"Market regime: {payload['market_regime']}\n"
            f"RSI: {payload['rsi']}\n"
            f"MACD: {payload['macd']}\n"
            f"Bollinger: {payload['bollinger']}\n"
            f"SAR: {payload['sar']}\n"
            f"Support/Resistance: {payload['support_resistance']}\n"
            f"Reasons supporting: {payload['reasons_supporting']}\n"
            f"Reasons against: {payload['reasons_against']}\n"
            "Explain this signal in plain language per your instructions."
        )
        return self._complete(prompt)

    def review_backtest(self, report_dict: Dict[str, Any]) -> str:
        stats = report_dict.get("stats", {})
        prompt = (
            f"Asset: {report_dict.get('asset')}\n"
            f"Timeframe: {report_dict.get('timeframe')}\n"
            f"Stats: {stats}\n"
            "Summarize these HISTORICAL SIMULATION backtest results in plain "
            "language, calling out any weak sample size, high drawdown, or "
            "inconsistency across confidence levels or market regimes. "
            "Explicitly state this is historical simulation, not a forecast."
        )
        return self._complete(prompt)
