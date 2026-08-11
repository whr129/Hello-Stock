from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from news_agent.markets.hours import is_us_market_open
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


PipelineRunState = dict[str, datetime]


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
                seconds=max(
                    self.settings.news_fetch_interval_seconds * 2,
                    self.settings.market_price_pipeline_interval_seconds * 2,
                    self.settings.breaking_resources_pipeline_interval_seconds * 2,
                    300,
                )
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


async def run_scheduler_tick(
    settings: Settings,
    last_refresh_at: PipelineRunState | datetime | None,
    *,
    now: datetime | None = None,
) -> PipelineRunState:
    control = SchedulerControlService(settings)
    memory_service = MemoryConsolidationService(control.session_factory, settings)
    now = now or datetime.now(UTC)
    last_runs = _coerce_pipeline_run_state(last_refresh_at)

    for pipeline_name in _due_pipelines(settings, last_runs, now):
        if await control.can_start_refresh():
            await control.run_refresh(job_type=pipeline_name)
            last_runs[pipeline_name] = now

    await memory_service.process_due_jobs()
    await control.cleanup_expired_content()
    return last_runs


def _coerce_pipeline_run_state(value: PipelineRunState | datetime | None) -> PipelineRunState:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, datetime):
        return {
            "market_prices": value,
            "breaking_resources": value,
            "daily_resources": value,
        }
    return {}


def _due_pipelines(
    settings: Settings,
    last_runs: PipelineRunState,
    now: datetime,
) -> list[str]:
    due: list[str] = []
    if is_us_market_open(now) and _pipeline_due(
        last_runs.get("market_prices"),
        now,
        settings.market_price_pipeline_interval_seconds,
    ):
        due.append("market_prices")
    if _pipeline_due(
        last_runs.get("breaking_resources"),
        now,
        settings.breaking_resources_pipeline_interval_seconds,
    ):
        due.append("breaking_resources")
    if _pipeline_due(
        last_runs.get("daily_resources"),
        now,
        settings.daily_resources_pipeline_interval_seconds,
    ):
        due.append("daily_resources")
    return due


def _pipeline_due(last_run_at: datetime | None, now: datetime, interval_seconds: int) -> bool:
    if last_run_at is None:
        return True
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (now - last_run_at).total_seconds() >= max(interval_seconds, 0)
