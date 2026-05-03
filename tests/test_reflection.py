from types import SimpleNamespace

import pytest

from news_agent.agent.reflection import (
    ReflectionDecision,
    ReflectionService,
    _decision_from_payload,
)
from news_agent.settings import Settings


def test_reflection_payload_retry_requires_supported_intent() -> None:
    decision = _decision_from_payload(
        {
            "verdict": "retry",
            "reason": "bad route",
            "corrected_intent": "unsupported",
            "corrected_args": ["AAPL"],
        }
    )

    assert decision.verdict == "pass"
    assert decision.corrected_intent is None


def test_reflection_payload_normalizes_retry_args() -> None:
    decision = _decision_from_payload(
        {
            "verdict": "retry",
            "reason": "stock request used search",
            "corrected_intent": "stocks",
            "corrected_args": ["aapl", "AAPL", " nvda "],
        }
    )

    assert decision == ReflectionDecision(
        verdict="retry",
        reason="stock request used search",
        corrected_intent="stocks",
        corrected_args=["AAPL", "NVDA"],
    )


@pytest.mark.asyncio
async def test_reflection_service_without_client_passes_unavailable() -> None:
    service = ReflectionService(Settings(openai_api_key=""))
    service.client = None

    decision = await service.reflect({"message_text": "what is AAPL doing?"})

    assert decision.verdict == "pass"
    assert decision.status == "unavailable"


@pytest.mark.asyncio
async def test_reflection_service_invalid_model_response_passes_unavailable() -> None:
    class BadCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{not-json"))]
            )

    service = ReflectionService(Settings(openai_api_key="test"))
    service.client = SimpleNamespace(chat=SimpleNamespace(completions=BadCompletions()))

    decision = await service.reflect({"message_text": "what is AAPL doing?"})

    assert decision.verdict == "pass"
    assert decision.status == "unavailable"
