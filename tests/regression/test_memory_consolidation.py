import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import Request
from openai import APIError

from news_agent.memory import consolidation
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


@pytest.fixture
def memory_job(monkeypatch):
    job = SimpleNamespace(id=1, user_id=7, source_start_event_id=10, source_end_event_id=20)
    events = [ConversationEvent(user_id=7, chat_id=7, role="user", content="Call me Howard.")]
    jobs = SimpleNamespace(
        mark_running=AsyncMock(return_value=job),
        get=AsyncMock(return_value=job),
        mark_completed=AsyncMock(),
        mark_failed=AsyncMock(),
        has_active_job=AsyncMock(return_value=False),
    )
    users = SimpleNamespace(update_memory_cursor=AsyncMock())
    event_repo = SimpleNamespace(
        list_between_ids=AsyncMock(return_value=events),
        list_oldest_unprocessed_user_events=AsyncMock(return_value=[]),
    )

    @asynccontextmanager
    async def session_scope(_factory):
        yield SimpleNamespace()

    monkeypatch.setattr(consolidation, "session_scope", session_scope)
    monkeypatch.setattr(consolidation, "MemoryConsolidationJobRepository", lambda session: jobs)
    monkeypatch.setattr(consolidation, "ConversationEventRepository", lambda session: event_repo)
    monkeypatch.setattr(consolidation, "UserRepository", lambda session, settings: users)
    service = _service()
    service.trace_service = SimpleNamespace(
        ensure_run=AsyncMock(return_value=1),
        start_step=AsyncMock(return_value=2),
        finish_step=AsyncMock(),
        finish_run=AsyncMock(),
        record_error=AsyncMock(return_value=3),
    )
    service.alert_service = SimpleNamespace(send_alert=AsyncMock())
    return service, jobs, users


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["api", "schema"])
async def test_extraction_failure_retries_job_without_consuming_transcript(
    memory_job, failure: str
) -> None:
    service, jobs, users = memory_job
    service.client = FakeClient({"candidates": "invalid"})
    if failure == "api":
        service.client.chat.completions.create = AsyncMock(
            side_effect=APIError(
                "provider unavailable", Request("POST", "https://example.com"), body=None
            )
        )

    await service._process_job(1)

    jobs.mark_failed.assert_awaited_once()
    assert jobs.mark_failed.await_args.args == (1,)
    assert jobs.mark_failed.await_args.kwargs["error_message"]
    assert (
        jobs.mark_failed.await_args.kwargs["max_retries"]
        == service.settings.memory_job_max_retries
    )
    users.update_memory_cursor.assert_not_awaited()
    jobs.mark_completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_empty_extraction_completes_job_and_advances_cursor(memory_job) -> None:
    service, jobs, users = memory_job
    service.client = FakeClient({"candidates": []})

    await service._process_job(1)

    jobs.mark_completed.assert_awaited_once_with(1)
    users.update_memory_cursor.assert_awaited_once_with(7, 20)
    jobs.mark_failed.assert_not_awaited()


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
async def test_extract_candidates_uses_llm_schema() -> None:
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

    candidates = await service.extract_candidates(
        [
            ConversationEvent(user_id=1, chat_id=1, role="user", content="ok call me Howard"),
            ConversationEvent(user_id=1, chat_id=1, role="assistant", content="Got it, Howard."),
        ]
    )

    assert candidates == [
        MemoryCandidate(
            text="User's preferred name is Howard.",
            category="profile",
            confidence=0.93,
        )
    ]
    messages = fake_client.chat.completions.kwargs["messages"]
    assert "Transcript:" in messages[1]["content"]
    assert "user: ok call me Howard" in messages[1]["content"]
    assert "assistant: Got it, Howard." in messages[1]["content"]
    response_format = fake_client.chat.completions.kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "memory_text",
    [
        "User lives in Toronto.",
        "User's API key is secret-value.",
        "User wants a technology watchlist.",
    ],
)
async def test_extract_candidates_rejects_removed_or_sensitive_memory(
    memory_text: str,
) -> None:
    service = _service()
    service.client = FakeClient(
        {
            "candidates": [
                {"text": memory_text, "category": "profile", "confidence": 0.95}
            ]
        }
    )

    candidates = await service.extract_candidates(
        [ConversationEvent(user_id=1, chat_id=1, role="user", content=memory_text)]
    )

    assert candidates == []


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
async def test_consolidate_candidate_rejects_memory_id_outside_supplied_pool() -> None:
    service = _service()
    service.client = FakeClient(
        {
            "action": "update",
            "memory_id": 99,
            "text": "User prefers concise English replies.",
            "category": "preference",
            "confidence": 0.9,
        }
    )
    nearest = [
        (
            LongTermMemory(
                id=7,
                user_id=1,
                memory_type="learned",
                memory_text="User prefers English replies.",
                category="preference",
                status="active",
                source="memory_job",
                confidence=0.7,
            ),
            0.1,
        )
    ]

    decision = await service.consolidate_candidate(
        MemoryCandidate(
            text="User prefers concise English replies.",
            category="preference",
            confidence=0.9,
        ),
        nearest,
    )

    assert decision.action == "skip"
    assert decision.memory_id is None


def test_memory_prompts_exclude_removed_personalization_and_treat_data_as_untrusted() -> None:
    from news_agent.memory.consolidation import EXTRACTION_PROMPT

    assert "local-news preferences" not in EXTRACTION_PROMPT
    assert "watch_habit" not in EXTRACTION_PROMPT
    assert "untrusted data" in EXTRACTION_PROMPT


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
