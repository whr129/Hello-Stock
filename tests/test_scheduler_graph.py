import pytest

from news_agent.graph.scheduler_graph import build_scheduler_graph
from news_agent.settings import Settings


class DummySchedulerNodes:
    def traced(self, step_name, func):
        del step_name
        return func

    def _step(self, state, name):
        return {
            **state,
            "metadata": {
                **state.get("metadata", {}),
                "steps": [*state.get("metadata", {}).get("steps", []), name],
            },
        }

    async def load_due_sources(self, state):
        return {
            **self._step(state, "load_due_sources"),
            "due_sources": [{"name": "Example"}],
            "due_tickers": ["AAPL"],
        }

    async def fetch_parallel(self, state):
        return {
            **self._step(state, "fetch_parallel"),
            "fetched_articles": [{"title": "Example headline"}],
        }

    async def normalize_dedupe(self, state):
        return {
            **self._step(state, "normalize_dedupe"),
            "saved_articles": [{"id": 1, "title": "Example headline"}],
        }

    async def embed_store(self, state):
        return self._step(state, "embed_store")

    async def precompute_summaries(self, state):
        return {**self._step(state, "precompute_summaries"), "summaries": ["Example summary"]}

    async def extract_mentions(self, state):
        state = self._step(state, "extract_mentions")
        return {**state, "metadata": {**state.get("metadata", {}), "mention_count": 1}}

    async def sector_enrichment(self, state):
        state = self._step(state, "sector_enrichment")
        return {**state, "metadata": {**state.get("metadata", {}), "sector_context_count": 1}}

    async def evidence_backfill(self, state):
        state = self._step(state, "evidence_backfill")
        return {
            **state,
            "metadata": {**state.get("metadata", {}), "signal_evidence_backfill_count": 1},
        }

    async def score_signals(self, state):
        state = self._step(state, "score_signals")
        return {**state, "metadata": {**state.get("metadata", {}), "signal_count": 1}}

    async def confidence_filter(self, state):
        state = self._step(state, "confidence_filter")
        return {**state, "metadata": {**state.get("metadata", {}), "confident_signal_count": 1}}

    async def quality_check(self, state):
        return self._step(state, "quality_check")

    async def cleanup_market_research(self, state):
        return self._step(state, "cleanup_market_research")

    async def retry_or_recover(self, state):
        state = self._step(state, "retry_or_recover")
        return {
            **state,
            "metadata": {**state.get("metadata", {}), "done": True},
        }


@pytest.mark.asyncio
async def test_scheduler_graph_runs_all_nodes(monkeypatch) -> None:
    monkeypatch.setattr(
        "news_agent.graph.scheduler_graph.SchedulerNodes",
        lambda session_factory, settings: DummySchedulerNodes(),
    )

    graph = build_scheduler_graph(session_factory=None, settings=Settings(openai_api_key=""))
    result = await graph.ainvoke({"job_type": "test", "errors": [], "metadata": {}})

    assert result["summaries"] == ["Example summary"]
    assert result["metadata"]["done"] is True
    assert result["metadata"]["steps"] == [
        "load_due_sources",
        "fetch_parallel",
        "normalize_dedupe",
        "embed_store",
        "precompute_summaries",
        "extract_mentions",
        "sector_enrichment",
        "evidence_backfill",
        "score_signals",
        "confidence_filter",
        "quality_check",
        "cleanup_market_research",
        "retry_or_recover",
    ]
