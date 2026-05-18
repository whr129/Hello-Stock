from news_agent.agent.router import extract_stock_symbols
from news_agent.research.schemas import (
    ResearchConstraints,
    ResearchEntities,
    ResearchPlan,
)


class PlannerAgent:
    """Deterministic first-pass planner for market research flows."""

    def plan(
        self,
        *,
        command: str,
        args: list[str],
        message_text: str,
    ) -> ResearchPlan:
        tickers = _ticker_args(args) or extract_stock_symbols(message_text)
        if command == "/signals":
            return ResearchPlan(
                task_type="stock_lookup",
                entities=ResearchEntities(tickers=tickers[:1]),
                research_horizon="30d",
                agents_to_run=["news", "market", "memory", "analysis", "report"],
                output_format="telegram_summary",
                constraints=ResearchConstraints(max_candidates=1, include_weak_evidence=True),
                command=command,
                query=message_text,
            )
        if command == "/research":
            return ResearchPlan(
                task_type="deep_research",
                entities=ResearchEntities(tickers=tickers),
                research_horizon="30d",
                agents_to_run=["news", "market", "memory", "analysis", "report"],
                output_format="telegram_summary",
                constraints=ResearchConstraints(max_candidates=5, include_weak_evidence=True),
                command=command,
                query=message_text,
            )
        if command == "/researchstatus":
            return ResearchPlan(
                task_type="alert_review",
                entities=ResearchEntities(),
                research_horizon="7d",
                agents_to_run=["analysis", "report"],
                output_format="telegram_summary",
                constraints=ResearchConstraints(max_candidates=5),
                command=command,
                query=message_text,
            )
        return ResearchPlan(
            task_type="candidate_ranking",
            entities=ResearchEntities(tickers=tickers),
            research_horizon="30d",
            agents_to_run=["analysis", "report"],
            output_format="telegram_summary",
            constraints=ResearchConstraints(max_candidates=5, include_weak_evidence=True),
            command=command or "/candidates",
            query=message_text,
        )


def _ticker_args(args: list[str]) -> list[str]:
    return sorted(
        dict.fromkeys(item.upper() for item in args if item.isalpha() and 1 <= len(item) <= 5)
    )
