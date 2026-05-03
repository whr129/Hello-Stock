import json
from types import SimpleNamespace

import pytest

from news_agent.memory.consolidation import (
    CONSOLIDATION_PROMPT,
    MemoryCandidate,
    MemoryConsolidationService,
    MemoryDecision,
)
from news_agent.settings import Settings
from news_agent.storage.models import ConversationEvent, LongTermMemory


def _service() -> MemoryConsolidationService:
    service = MemoryConsolidationService(session_factory=None, settings=Settings(openai_api_key=""))  # type: ignore[arg-type]
    service.client = None
    return service


class FakeCompletions:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.kwargs = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.payload)),
                )
            ]
        )


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(payload))


@pytest.mark.asyncio
async def test_extract_candidates_without_llm_returns_empty() -> None:
    service = _service()
    events = [
        ConversationEvent(user_id=1, chat_id=1, role="user", content="I prefer AI news."),
        ConversationEvent(user_id=1, chat_id=1, role="assistant", content="Noted."),
    ]

    candidates = await service.extract_candidates(events)

    assert candidates == []


@pytest.mark.asyncio
async def test_extract_turn_candidates_uses_llm_schema() -> None:
    service = _service()
    fake_client = FakeClient(
        {
            "candidates": [
                {
                    "text": "User's preferred name is Howard.",
                    "category": "profile",
                    "confidence": 0.93,
                }
            ]
        }
    )
    service.client = fake_client

    candidates = await service.extract_turn_candidates(
        user_message="ok call me Howard",
        assistant_response="Got it, Howard.",
    )

    assert candidates == [
        MemoryCandidate(
            text="User's preferred name is Howard.",
            category="profile",
            confidence=0.93,
        )
    ]
    messages = fake_client.chat.completions.kwargs["messages"]
    assert "Latest turn:" in messages[1]["content"]
    assert "ok call me Howard" in messages[1]["content"]


@pytest.mark.asyncio
async def test_consolidate_candidate_fallback_updates_close_match() -> None:
    service = _service()
    nearest = [
        (
            LongTermMemory(
                id=7,
                user_id=1,
                memory_type="learned",
                memory_text="User prefers AI news.",
                category="preference",
                status="active",
                source="memory_job",
                confidence=0.7,
            ),
            0.1,
        )
    ]

    decision = await service.consolidate_candidate(
        MemoryCandidate(text="I prefer AI news.", category="preference", confidence=0.8),
        nearest,
    )

    assert decision.action == "update"
    assert decision.memory_id == 7


@pytest.mark.asyncio
async def test_consolidate_candidate_normalizes_llm_action() -> None:
    service = _service()
    service.client = FakeClient(
        {
            "action": "merge",
            "memory_id": "7",
            "text": "User prefers AI news.",
            "category": "preference",
            "confidence": 0.8,
        }
    )

    decision = await service.consolidate_candidate(
        MemoryCandidate(text="User prefers AI news.", category="preference", confidence=0.8),
        [],
    )

    assert decision.action == "skip"
    assert decision.memory_id is None


@pytest.mark.asyncio
async def test_embedding_for_decision_embeds_rewritten_memory_text() -> None:
    service = _service()
    calls: list[str] = []

    async def fake_embed(text: str) -> list[float]:
        calls.append(text)
        return [0.25]

    service.embedding_service.embed_text = fake_embed  # type: ignore[method-assign]

    embedding = await service._embedding_for_decision(
        candidate=MemoryCandidate(
            text="I prefer AI news.",
            category="preference",
            confidence=0.7,
        ),
        candidate_embedding=[0.1],
        decision=MemoryDecision(
            action="update",
            memory_id=1,
            text="User prefers AI news.",
            category="preference",
            confidence=0.8,
        ),
    )

    assert embedding == [0.25]
    assert calls == ["User prefers AI news."]


def test_consolidation_prompt_preserves_semantic_context() -> None:
    assert "semantically equivalent" in CONSOLIDATION_PROMPT
    assert "likes pizza" in CONSOLIDATION_PROMPT
    assert "loves pizza" in CONSOLIDATION_PROMPT
