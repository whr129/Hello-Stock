import pytest

from news_agent.graph.scheduler_graph import build_scheduler_graph
from news_agent.settings import Settings


class DummySchedulerNodes:
    async def load_due_sources(self, state):
        return {**state, "due_sources": [{"name": "Example"}], "due_tickers": ["AAPL"]}

    async def fetch_parallel(self, state):
        return {**state, "fetched_articles": [{"title": "Example headline"}]}

    async def normalize_dedupe(self, state):
        return {**state, "saved_articles": [{"id": 1, "title": "Example headline"}]}

    async def embed_store(self, state):
        return state

    async def precompute_summaries(self, state):
        return {**state, "summaries": ["Example summary"]}

    async def quality_check(self, state):
        return state

    async def retry_or_recover(self, state):
        return {**state, "metadata": {"done": True}}


@pytest.mark.asyncio
async def test_scheduler_graph_runs_all_nodes(monkeypatch) -> None:
    monkeypatch.setattr(
        "news_agent.graph.scheduler_graph.SchedulerNodes",
        lambda session_factory, settings: DummySchedulerNodes(),
    )

    graph = build_scheduler_graph(session_factory=None, settings=Settings(openai_api_key=""))
    result = await graph.ainvoke({"job_type": "test", "errors": [], "metadata": {}})

    assert result["summaries"] == ["Example summary"]
    assert result["metadata"] == {"done": True}
