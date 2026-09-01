from types import SimpleNamespace

import pytest

from news_agent.agent import tools as tool_module
from news_agent.settings import Settings
from news_agent.storage.repositories import MemoryRepository


@pytest.mark.asyncio
async def test_memory_repository_semantic_search_text(monkeypatch) -> None:
    repository = MemoryRepository(SimpleNamespace())
    rows = [SimpleNamespace(memory_text="one"), SimpleNamespace(memory_text="two")]

    async def semantic_search_for_user(**kwargs):
        assert kwargs["user_id"] == 7
        return rows

    monkeypatch.setattr(repository, "semantic_search_for_user", semantic_search_for_user)
    assert await repository.semantic_search_text(user_id=7, query_embedding=[0.0]) == [
        "one",
        "two",
    ]


@pytest.mark.asyncio
async def test_main_agent_memory_search_tool(monkeypatch) -> None:
    seen = {}

    class FakeEmbeddingService:
        def __init__(self, settings):
            del settings

        async def embed_text(self, query):
            seen["query"] = query
            return [0.0]

    class FakeMemoryRepository:
        def __init__(self, session):
            del session

        async def semantic_search_text(self, **kwargs):
            seen.update(kwargs)
            return ["remembered fact"]

    class SessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

    monkeypatch.setattr(tool_module, "EmbeddingService", FakeEmbeddingService)
    monkeypatch.setattr(tool_module, "MemoryRepository", FakeMemoryRepository)
    built = tool_module.build_main_tools(
        SessionFactory(),
        Settings(openai_api_key=""),
        research_agent=object(),
        runtime_agent=object(),
    )
    result = await built["memory_search"].execute("query", user_id=7)
    assert result == "Relevant stored memory:\n- remembered fact"
    assert seen["query"] == "query"
    assert seen["user_id"] == 7


@pytest.mark.asyncio
async def test_research_tool_uses_signal_route_for_one_ticker() -> None:
    seen = {}

    class FakeResearchAgent:
        async def run(self, state):
            seen.update(state)
            return {"response": "signal result"}

    execute = tool_module._research_execute(FakeResearchAgent())

    result = await execute("Why is Nvidia ranked?", tickers=["nvda"])

    assert result == "signal result"
    assert seen["command"] == "/signals"
    assert seen["args"] == ["NVDA"]


@pytest.mark.asyncio
async def test_research_tool_uses_research_route_without_tickers() -> None:
    seen = {}

    class FakeResearchAgent:
        async def run(self, state):
            seen.update(state)
            return {"response": "research result"}

    execute = tool_module._research_execute(FakeResearchAgent())

    result = await execute("What changed today?", tickers=[])

    assert result == "research result"
    assert seen["command"] == "/research"
