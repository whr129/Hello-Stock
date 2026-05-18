from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from news_agent.memory.consolidation import MemoryConsolidationService
from news_agent.settings import Settings
from news_agent.storage.database import create_session_factory
from news_agent.storage.repositories import (
    ArticleRepository,
    ConversationEventRepository,
    JobRepository,
    MarketRepository,
    RuntimeRunRepository,
    ShortTermSessionRepository,
    SummaryRepository,
)


@dataclass(frozen=True)
class RefreshSummary:
    job_type: str
    saved_article_count: int
    summary_count: int
    market_snapshot_count: int
    error_count: int
    provider_counts: dict[str, int]
    errors: list[str]


def parse_config_value(raw: str) -> object:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


class SchedulerControlService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session_factory = create_session_factory(settings)

    async def can_start_refresh(self) -> bool:
        async with self.session_factory() as session:
            stale_cutoff = datetime.now(UTC) - timedelta(
                seconds=max(self.settings.news_fetch_interval_seconds * 2, 300)
            )
            await JobRepository(session).recover_stale_running_jobs(stale_cutoff)
            await session.commit()
            return not await JobRepository(session).has_running_job()

    async def run_refresh(self, job_type: str = "manual_refresh") -> RefreshSummary:
        from news_agent.scheduler.jobs import run_scheduler_once

        result = await run_scheduler_once(job_type=job_type, settings=self.settings)
        metadata = result.get("metadata", {})
        return RefreshSummary(
            job_type=job_type,
            saved_article_count=metadata.get("saved_article_count", 0),
            summary_count=len(result.get("summaries", [])),
            market_snapshot_count=metadata.get("market_snapshot_count", 0),
            error_count=len(result.get("errors", [])),
            provider_counts=dict(metadata.get("provider_counts", {})),
            errors=list(result.get("errors", [])),
        )

    def format_refresh_summary(self, summary: RefreshSummary) -> str:
        provider_text = ", ".join(
            f"{provider}: {count}" for provider, count in sorted(summary.provider_counts.items())
        ) or "none"
        lines = [
            f"Refresh completed.\n"
            f"- Articles saved: {summary.saved_article_count}\n"
            f"- Summaries generated: {summary.summary_count}\n"
            f"- Market snapshots refreshed: {summary.market_snapshot_count}\n"
            f"- Provider items fetched: {provider_text}\n"
            f"- Errors: {summary.error_count}"
        ]
        if summary.errors:
            lines.append("Error details:")
            lines.extend(f"- {error}" for error in summary.errors)
        return "\n".join(lines)

    async def cleanup_expired_content(self) -> dict[str, int]:
        now = datetime.now(UTC)
        article_cutoff = now - timedelta(days=self.settings.article_retention_days)
        snapshot_cutoff = now - timedelta(days=self.settings.snapshot_retention_days)
        job_cutoff = now - timedelta(days=self.settings.job_run_retention_days)
        runtime_cutoff = now - timedelta(days=self.settings.runtime_retention_days)
        event_cutoff = now - timedelta(days=self.settings.conversation_event_retention_days)

        async with self.session_factory() as session:
            summary_deleted = await SummaryRepository(session).delete_created_before(article_cutoff)
            article_deleted = await ArticleRepository(session).delete_created_before(article_cutoff)
            snapshot_deleted = await MarketRepository(session).delete_captured_before(
                snapshot_cutoff
            )
            job_deleted = await JobRepository(session).delete_started_before(job_cutoff)
            runtime_deleted = await RuntimeRunRepository(session).delete_started_before(
                runtime_cutoff
            )
            session_deleted = await ShortTermSessionRepository(session).delete_expired_before(now)
            event_deleted = await ConversationEventRepository(session).delete_created_before(
                event_cutoff
            )
            await session.commit()

        return {
            "summaries": summary_deleted,
            "articles": article_deleted,
            "snapshots": snapshot_deleted,
            "job_runs": job_deleted,
            "runtime_runs": runtime_deleted,
            "short_term_sessions": session_deleted,
            "conversation_events": event_deleted,
        }


async def run_scheduler_tick(settings: Settings, last_refresh_at: datetime | None) -> datetime:
    control = SchedulerControlService(settings)
    memory_service = MemoryConsolidationService(control.session_factory, settings)
    now = datetime.now(UTC)
    refresh_due = last_refresh_at is None or (
        now - last_refresh_at
    ) >= timedelta(seconds=settings.news_fetch_interval_seconds)
    if refresh_due and await control.can_start_refresh():
        await control.run_refresh(job_type="market_research_refresh")
        last_refresh_at = now
    await memory_service.process_due_jobs()
    await control.cleanup_expired_content()
    return last_refresh_at or now
