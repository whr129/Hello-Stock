from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from news_agent.research.schemas import (
    CandidateExplanation,
    CompanyResearchPacket,
    CompanyResearchStatus,
    ResearchPlan,
)
from news_agent.research.web_search import ResearchWebSearchService
from news_agent.settings import Settings

_LIVE_RESEARCH_TERMS = (
    "current",
    "latest",
    "recent",
    "today",
    "overview",
    "company",
    "financial",
    "financials",
    "filing",
    "10-k",
    "10-q",
    "8-k",
    "earnings",
    "news",
    "catalyst",
    "risk",
    "compare",
    "comparison",
)


class CompanyResearchCoordinator:
    def __init__(
        self,
        settings: Settings,
        *,
        service: ResearchWebSearchService | None = None,
    ) -> None:
        self.settings = settings
        self.service = service or ResearchWebSearchService(settings)

    async def research_many(
        self,
        candidates: Sequence[CandidateExplanation],
        *,
        query: str,
        horizon: str,
    ) -> list[CompanyResearchPacket]:
        maximum = min(max(self.settings.research_web_max_companies, 1), 5)
        selected = [candidate for candidate in candidates if candidate.ticker][:maximum]
        if not selected:
            return []

        semaphore = asyncio.Semaphore(
            min(max(self.settings.research_web_concurrency, 1), maximum)
        )

        async def worker(candidate: CandidateExplanation) -> CompanyResearchPacket:
            ticker = (candidate.ticker or "").upper()
            company_name = _company_name(ticker, self.settings)
            try:
                async with semaphore:
                    return await asyncio.wait_for(
                        self.service.research_company(
                            ticker=ticker,
                            company_name=company_name,
                            theme=candidate.theme or "",
                            query=query,
                            horizon=horizon,
                        ),
                        timeout=min(
                            max(self.settings.research_web_company_timeout_seconds, 5),
                            120,
                        ),
                    )
            except TimeoutError:
                return _worker_failure(ticker, company_name, "timeout", "company_timeout")
            except Exception:
                return _worker_failure(ticker, company_name, "failed", "worker_error")

        tasks = [asyncio.create_task(worker(candidate)) for candidate in selected]
        done, pending = await asyncio.wait(
            tasks,
            timeout=min(max(self.settings.research_web_total_timeout_seconds, 5), 180),
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        results_by_task = {task: task.result() for task in done}
        results: list[CompanyResearchPacket] = []
        for candidate, task in zip(selected, tasks, strict=True):
            ticker = (candidate.ticker or "").upper()
            company_name = _company_name(ticker, self.settings)
            results.append(
                results_by_task.get(
                    task,
                    _worker_failure(ticker, company_name, "timeout", "global_timeout"),
                )
            )
        return results


def should_enrich_research(
    plan: ResearchPlan,
    candidates: Sequence[CandidateExplanation],
    settings: Settings,
) -> bool:
    if not settings.research_web_enabled or not settings.openai_api_key:
        return False
    if not any(candidate.ticker for candidate in candidates):
        return False
    if plan.command == "/research":
        return True
    query = plan.query.lower()
    return any(term in query for term in _LIVE_RESEARCH_TERMS)


def _company_name(ticker: str, settings: Settings) -> str:
    try:
        aliases = json.loads(settings.market_entity_aliases_json)
    except (TypeError, ValueError):
        return ticker
    values = aliases.get(ticker)
    if isinstance(values, list) and values and isinstance(values[0], str):
        return values[0].strip().title() or ticker
    return ticker


def _worker_failure(
    ticker: str,
    company_name: str,
    status: CompanyResearchStatus,
    error: str,
) -> CompanyResearchPacket:
    return CompanyResearchPacket(
        ticker=ticker,
        company_name=company_name,
        identity_status="unresolved",
        as_of=datetime.now(UTC),
        status=status,
        errors=[error],
    )
