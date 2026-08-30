import json
from types import SimpleNamespace

import pytest

from news_agent.research import agents
from news_agent.research.agents import ResearchSubagent
from news_agent.settings import Settings


@pytest.mark.asyncio
async def test_signals_without_ticker_returns_usage_without_research_report() -> None:
    agent = ResearchSubagent(session_factory=None, settings=Settings(openai_api_key=""))

    result = await agent.run(
        {
            "command": "/signals",
            "args": [],
            "message_text": "/signals",
        }
    )

    assert result["response"] == "Usage: /signals <ticker>"
    assert result["metadata"]["status"] == "missing_ticker"


@pytest.mark.asyncio
async def test_research_command_runs_extended_pipeline(monkeypatch) -> None:
    calls: list[str] = []

    async def extract_market_mentions(session, settings, *, limit):
        del session, settings, limit
        calls.append("extract_mentions")
        return 2

    async def enrich_market_sectors(session, settings):
        del session, settings
        calls.append("sector_enrichment")
        return 3

    async def backfill_signal_evidence_links(session):
        del session
        calls.append("evidence_backfill")
        return 4

    async def score_market_signals(session, settings):
        del session, settings
        calls.append("score_signals")
        return 5

    async def count_confident_signal_context(session, settings):
        del session, settings
        calls.append("confidence_filter")
        return 6

    async def prune_market_research_data(session, settings):
        del session, settings
        calls.append("cleanup")
        return 7

    monkeypatch.setattr(agents, "extract_market_mentions", extract_market_mentions)
    monkeypatch.setattr(agents, "enrich_market_sectors", enrich_market_sectors)
    monkeypatch.setattr(agents, "backfill_signal_evidence_links", backfill_signal_evidence_links)
    monkeypatch.setattr(agents, "score_market_signals", score_market_signals)
    monkeypatch.setattr(agents, "count_confident_signal_context", count_confident_signal_context)
    monkeypatch.setattr(agents, "prune_market_research_data", prune_market_research_data)
    monkeypatch.setattr(agents, "MarketSignalRepository", _FakeMarketSignalRepository)

    agent = ResearchSubagent(
        session_factory=_FakeSessionFactory(), settings=Settings(openai_api_key="")
    )

    result = await agent.run(
        {
            "command": "/research",
            "args": ["semiconductor"],
            "message_text": "/research semiconductor AI infrastructure",
        }
    )

    assert calls == [
        "extract_mentions",
        "sector_enrichment",
        "evidence_backfill",
        "score_signals",
        "confidence_filter",
        "cleanup",
    ]
    assert result["metadata"]["sector_context_count"] == 3
    assert result["metadata"]["signal_evidence_backfill_count"] == 4
    assert result["metadata"]["confident_signal_count"] == 6
    assert {"AI", "AI infrastructure", "semiconductors"} <= set(result["metadata"]["sectors"])


@pytest.mark.asyncio
async def test_research_agent_llm_loop_feeds_web_enrichment(monkeypatch) -> None:
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="company_web_research",
            arguments=json.dumps({"companies": [{"ticker": "NVDA"}]}),
        ),
    )
    messages = [
        SimpleNamespace(content=None, tool_calls=[tool_call]),
        SimpleNamespace(content="gathered context", tool_calls=[]),
        SimpleNamespace(content="synthesized report", tool_calls=[]),
    ]

    class FakeCompletions:
        async def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=messages.pop(0))])

    packet = SimpleNamespace(
        ticker="NVDA",
        status="complete",
        evidence=[SimpleNamespace(title="Nvidia filing", url="https://example.com/filing")],
    )

    class FakeCoordinator:
        async def research_many(self, candidates, *, query, horizon):
            del candidates, query, horizon
            return [packet]

    monkeypatch.setattr(agents, "MarketSignalRepository", _FakeMarketSignalRepository)
    agent = ResearchSubagent(
        session_factory=_FakeSessionFactory(),
        settings=Settings(openai_api_key="test", research_web_enabled=True),
    )
    agent.research_llm_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    agent.company_research = FakeCoordinator()

    result = await agent.run(
        {"command": "/candidates", "args": ["NVDA"], "message_text": "/candidates NVDA"}
    )

    assert result["response"] == "synthesized report"
    assert result["metadata"]["research_llm_used"] is True
    assert result["metadata"]["research_web_llm_packet_count"] == 1


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSession()


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    async def commit(self):
        return None


class _FakeMarketSignalRepository:
    def __init__(self, session) -> None:
        del session

    async def fetch_top_candidates(self, *, window, limit, since):
        del window, limit, since
        return []
