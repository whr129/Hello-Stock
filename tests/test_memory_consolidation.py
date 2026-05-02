import pytest

from news_agent.memory.consolidation import MemoryCandidate, MemoryConsolidationService
from news_agent.settings import Settings
from news_agent.storage.models import ConversationEvent, LongTermMemory


def _service() -> MemoryConsolidationService:
    service = MemoryConsolidationService(session_factory=None, settings=Settings(openai_api_key=""))  # type: ignore[arg-type]
    service.client = None
    return service


@pytest.mark.asyncio
async def test_extract_candidates_fallback_uses_preference_markers() -> None:
    service = _service()
    events = [
        ConversationEvent(user_id=1, chat_id=1, role="user", content="I prefer AI news."),
        ConversationEvent(user_id=1, chat_id=1, role="assistant", content="Noted."),
    ]

    candidates = await service.extract_candidates(events)

    assert candidates == [
        MemoryCandidate(text="I prefer AI news.", category="preference", confidence=0.6)
    ]


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
