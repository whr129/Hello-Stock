import asyncio
from datetime import UTC, datetime

import pytest

from news_agent.research.orchestration import (
    CompanyResearchCoordinator,
    should_enrich_research,
)
from news_agent.research.schemas import (
    CandidateExplanation,
    CompanyResearchPacket,
    ResearchConstraints,
    ResearchEntities,
    ResearchPlan,
)
from news_agent.settings import Settings


def _candidate(ticker: str, rank: int) -> CandidateExplanation:
    return CandidateExplanation(
        ticker=ticker,
        theme="semiconductors",
        rank=rank,
        total_score=90 - rank,
        components={},
        evidence=[],
        weak_evidence=[],
    )


class _ConcurrencyService:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0

    async def research_company(self, **kwargs) -> CompanyResearchPacket:
        ticker = kwargs["ticker"]
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep({"NVDA": 0.03, "AMD": 0.01}.get(ticker, 0.02))
        self.active -= 1
        return CompanyResearchPacket(
            ticker=ticker,
            company_name=kwargs["company_name"],
            identity_status="matched",
            as_of=datetime.now(UTC),
            status="complete",
        )


@pytest.mark.asyncio
async def test_company_research_fanout_is_bounded_and_preserves_candidate_order() -> None:
    service = _ConcurrencyService()
    settings = Settings(
        openai_api_key="test",
        research_web_enabled=True,
        research_web_concurrency=2,
        research_web_max_companies=3,
    )
    coordinator = CompanyResearchCoordinator(settings, service=service)  # type: ignore[arg-type]

    packets = await coordinator.research_many(
        [_candidate("NVDA", 1), _candidate("AMD", 2), _candidate("MU", 3), _candidate("AAPL", 4)],
        query="latest",
        horizon="30d",
    )

    assert [packet.ticker for packet in packets] == ["NVDA", "AMD", "MU"]
    assert service.maximum_active == 2


def test_research_web_trigger_policy() -> None:
    settings = Settings(openai_api_key="test", research_web_enabled=True)
    candidate = _candidate("NVDA", 1)
    research = ResearchPlan(
        task_type="deep_research",
        entities=ResearchEntities(),
        research_horizon="30d",
        agents_to_run=[],
        output_format="telegram_summary",
        constraints=ResearchConstraints(),
        command="/research",
        query="/research",
    )
    candidates = ResearchPlan(
        task_type="candidate_ranking",
        entities=ResearchEntities(),
        research_horizon="30d",
        agents_to_run=[],
        output_format="telegram_summary",
        constraints=ResearchConstraints(),
        command="/candidates",
        query="/candidates",
    )

    assert should_enrich_research(research, [candidate], settings)
    assert not should_enrich_research(candidates, [candidate], settings)
    latest = ResearchPlan(**{**candidates.__dict__, "query": "latest company financials"})
    assert should_enrich_research(latest, [candidate], settings)
    assert not should_enrich_research(
        research,
        [candidate],
        Settings(openai_api_key="test", research_web_enabled=False),
    )
