"""Shared daily research result for scheduled, Telegram, and CLI callers."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from news_agent.agent.guardrails import enforce_financial_guardrails
from news_agent.harness.contracts import CapabilitySpec, Contract
from news_agent.research.analysis import explain_candidates, visible_candidate_explanations
from news_agent.research.link_validation import validate_candidate_links
from news_agent.research.reporting import format_candidates
from news_agent.settings import Settings
from news_agent.storage.repositories import MarketSignalRepository


class ReportArguments(Contract):
    pass


async def build_daily_research_result(
    session_factory, settings: Settings, now: datetime, *, report_date: str | None = None,
) -> dict:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    report_date = report_date or now.astimezone(
        ZoneInfo(settings.daily_research_report_timezone)
    ).date().isoformat()
    limit = settings.daily_research_report_max_candidates
    async with session_factory() as session:
        repository = MarketSignalRepository(session)
        snapshots = await repository.fetch_top_candidates(
            window="24h", limit=limit * 3, since=now - timedelta(hours=24)
        )
        explanations = await validate_candidate_links(
            explain_candidates(
                snapshots, min_strong_sources=settings.signal_min_strong_evidence_sources,
            ),
            recheck_hours=settings.evidence_link_recheck_hours,
        )
        for explanation in explanations:
            if explanation.snapshot_id is not None:
                await repository.update_snapshot_evidence(
                    explanation.snapshot_id, explanation.evidence
                )
        await session.commit()
    candidates = visible_candidate_explanations(
        explanations, limit=limit, include_developing=settings.signal_allow_developing_default,
    )
    report = format_candidates(
        candidates, max_evidence_items=settings.research_report_max_evidence_items,
    )
    alert_context = ""
    if settings.market_alerts_enabled:
        from news_agent.alerts.service import MarketAlertService

        alerts = MarketAlertService(session_factory, settings)
        try:
            alert_context = await alerts.report_context(now)
        finally:
            await alerts.aclose()
    response = (
        f"Daily research report — {report_date}\n"
        f"Timezone: {settings.daily_research_report_timezone}\n"
        "Latest stored 24-hour signals updated within the past day.\n\n"
        f"{enforce_financial_guardrails(report)}"
    )
    if alert_context:
        response += "\n\n" + alert_context
    return {
        "response": response,
        "as_of": now.isoformat(),
        "report_date": report_date,
        "candidates": [
            {
                "ticker": item.ticker, "theme": item.theme, "rank": item.rank,
                "score": item.total_score, "components": item.components,
                "evidence_strength": item.evidence_strength, "evidence": item.evidence,
                "gaps": item.weak_evidence,
            }
            for item in candidates
        ],
        "alert_context": alert_context,
    }


def daily_report_capability(session_factory, settings: Settings) -> CapabilitySpec:
    async def report(args, context):
        return await build_daily_research_result(
            session_factory, settings, context.as_of,
            report_date=context.data.get("report_date"),
        )

    return CapabilitySpec(
        id="research.report",
        description="Read stored candidates, events, and news sentiment with source evidence.",
        input_model=ReportArguments,
        handler=report,
        effect="cache",
        idempotent=True,
        timeout_seconds=90,
    )
