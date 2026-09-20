from types import SimpleNamespace

import pytest

from news_agent.agent.reflection import (
    ReflectionDecision,
    ReflectionService,
    _decision_from_payload,
)
from news_agent.settings import Settings


def test_reflection_payload_rejects_unknown_agent() -> None:
    decision = _decision_from_payload(
        {
            "verdict": "retry",
            "reason": "bad route",
            "corrected_agent": "news",
        }
    )

    assert decision.verdict == "retry"
    assert decision.corrected_agent is None


def test_reflection_payload_keeps_agent_and_hint() -> None:
    decision = _decision_from_payload(
        {
            "verdict": "retry",
            "reason": "research request used search",
            "corrected_agent": "research",
            "retry_hint": "use NVDA",
        }
    )

    assert decision == ReflectionDecision(
        verdict="retry",
        reason="research request used search",
        corrected_agent="research",
        retry_hint="use NVDA",
    )


def test_reflection_truncates_retry_hint() -> None:
    decision = _decision_from_payload(
        {
            "verdict": "retry",
            "reason": "weak answer",
            "corrected_agent": "web_search",
            "retry_hint": "x" * 600,
        }
    )

    assert decision.verdict == "retry"
    assert decision.corrected_agent == "web_search"
    assert len(decision.retry_hint) == 500


def test_reflection_truncates_long_reason() -> None:
    decision = _decision_from_payload(
        {
            "verdict": "retry",
            "reason": "x" * 600,
            "corrected_agent": "memory_search",
            "retry_hint": "search memory",
        }
    )

    assert decision.verdict == "retry"
    assert len(decision.reason) == 500


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
