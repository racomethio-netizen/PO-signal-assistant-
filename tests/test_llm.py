from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from llm.anthropic_provider import AnthropicProvider
from llm.provider import AI_UNAVAILABLE_MESSAGE
from signals.engine import SignalEngine
from signals.models import Confidence, MarketRegime, Signal, Direction
from strategy.config import TIMEFRAME_CONFIGS


def make_signal() -> Signal:
    return Signal(
        asset="TEST", timeframe="2m", generated_at=datetime.now(timezone.utc),
        direction=Direction.CALL, confidence=Confidence.HIGH, score=70.0,
        market_regime=MarketRegime.TRENDING_BULLISH, component_scores=[],
        trend={}, momentum={}, volatility={}, support_resistance={},
        macd={}, rsi={}, bollinger={}, sar={}, moving_averages={},
        price_action={}, multi_timeframe={},
        reasons_supporting=[], reasons_against=[],
        invalidation_condition="n/a", suggested_expiry_window="~2 minutes",
    )


def test_no_api_key_returns_unavailable_message():
    provider = AnthropicProvider(api_key=None)
    result = provider.explain_signal(make_signal())
    assert result == AI_UNAVAILABLE_MESSAGE


def test_client_exception_falls_back_to_unavailable_message():
    provider = AnthropicProvider(api_key="fake-key")
    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("network error")
        mock_get_client.return_value = mock_client
        result = provider.explain_signal(make_signal())
    assert result == AI_UNAVAILABLE_MESSAGE


def test_malformed_response_with_no_text_blocks_falls_back():
    provider = AnthropicProvider(api_key="fake-key")
    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        fake_response = MagicMock()
        fake_response.content = []  # no text blocks at all
        mock_client.messages.create.return_value = fake_response
        mock_get_client.return_value = mock_client
        result = provider.explain_signal(make_signal())
    assert result == AI_UNAVAILABLE_MESSAGE


def test_successful_response_is_returned_verbatim():
    provider = AnthropicProvider(api_key="fake-key")
    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        block = MagicMock()
        block.type = "text"
        block.text = "Technical alignment favors CALL based on trend and momentum."
        fake_response = MagicMock()
        fake_response.content = [block]
        mock_client.messages.create.return_value = fake_response
        mock_get_client.return_value = mock_client
        result = provider.explain_signal(make_signal())
    assert "Technical alignment" in result


def test_review_backtest_never_raises_on_failure():
    provider = AnthropicProvider(api_key="fake-key")
    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = ValueError("boom")
        mock_get_client.return_value = mock_client
        result = provider.review_backtest({"asset": "X", "timeframe": "2m", "stats": {}})
    assert result == AI_UNAVAILABLE_MESSAGE


def test_engine_falls_back_gracefully_when_llm_provider_raises(uptrend_candles):
    class ExplodingProvider:
        def explain_signal(self, signal):
            raise RuntimeError("provider is down")

        def review_backtest(self, report_dict):
            raise RuntimeError("provider is down")

    engine = SignalEngine(llm_provider=ExplodingProvider())
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles, explain_with_llm=True)
    assert sig.explanation == AI_UNAVAILABLE_MESSAGE


def test_engine_marks_unavailable_when_no_provider_configured(uptrend_candles):
    engine = SignalEngine(llm_provider=None)
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles, explain_with_llm=True)
    assert sig.explanation == AI_UNAVAILABLE_MESSAGE


def test_engine_leaves_explanation_none_when_not_requested(uptrend_candles):
    engine = SignalEngine(llm_provider=None)
    sig = engine.generate("TEST", TIMEFRAME_CONFIGS["2m"], uptrend_candles, explain_with_llm=False)
    assert sig.explanation is None
