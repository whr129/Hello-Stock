from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Bot

from news_agent.agent.chains import build_brief_response
from news_agent.memory.consolidation import MemoryConsolidationService
from news_agent.settings import Settings
from news_agent.storage.database import create_session_factory
from news_agent.storage.models import User
from news_agent.storage.repositories import (
    ArticleRepository,
    JobRepository,
    MarketRepository,
    PreferenceRepository,
    RuntimeRunRepository,
    SummaryRepository,
    TickerRepository,
)
from news_agent.storage.retrieval import RetrievalService


@dataclass(frozen=True)
class RefreshSummary:
    job_type: str
    saved_article_count: int
    summary_count: int
    market_snapshot_count: int
    error_count: int
    provider_counts: dict[str, int]
    errors: list[str]


def validate_delivery_time(value: str) -> str:
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.strftime("%H:%M")


def validate_timezone(value: str) -> str:
    ZoneInfo(value)
    return value


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


def should_send_daily_recap(
    *,
    now_utc: datetime,
    timezone_name: str,
    delivery_time: str | None,
    last_sent_at: datetime | None,
) -> bool:
    if not delivery_time:
        return False
    local_now = now_utc.astimezone(ZoneInfo(timezone_name))
    hour, minute = [int(part) for part in delivery_time.split(":", 1)]
    if (local_now.hour, local_now.minute) < (hour, minute):
        return False
    if last_sent_at is None:
        return True
    return last_sent_at.astimezone(ZoneInfo(timezone_name)).date() < local_now.date()


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

        async with self.session_factory() as session:
            summary_deleted = await SummaryRepository(session).delete_created_before(article_cutoff)
            article_deleted = await ArticleRepository(session).delete_created_before(article_cutoff)
            snapshot_deleted = await MarketRepository(session).delete_captured_before(snapshot_cutoff)
            job_deleted = await JobRepository(session).delete_started_before(job_cutoff)
            runtime_deleted = await RuntimeRunRepository(session).delete_started_before(runtime_cutoff)
            await session.commit()

        return {
            "summaries": summary_deleted,
            "articles": article_deleted,
            "snapshots": snapshot_deleted,
            "job_runs": job_deleted,
            "runtime_runs": runtime_deleted,
        }


class DailyRecapService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session_factory = create_session_factory(settings)
        self.bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token else None

    async def send_due_recaps(self, now: datetime | None = None) -> int:
        if self.bot is None:
            return 0

        sent = 0
        current = now or datetime.now(UTC)
        async with self.session_factory() as session:
            pairs = await PreferenceRepository(session).list_with_delivery_time()

            for user, preference in pairs:
                if not should_send_daily_recap(
                    now_utc=current,
                    timezone_name=user.timezone,
                    delivery_time=preference.delivery_time,
                    last_sent_at=preference.last_daily_recap_sent_at,
                ):
                    continue
                message = await self._build_recap(session, user)
                await self.bot.send_message(chat_id=user.telegram_user_id, text=message)
                await PreferenceRepository(session).mark_daily_recap_sent(user.id, current)
                sent += 1

            await session.commit()
        return sent

    async def _build_recap(self, session, user: User) -> str:
        tickers = await TickerRepository(session).list_for_user(user.id)
        preference = await PreferenceRepository(session).get_for_user(user.id)
        context = await RetrievalService(session).retrieve_for_brief(
            user_id=user.id,
            topics=preference.topics,
            tickers=tickers,
            article_max_age_hours=self.settings.news_freshness_hours,
            summary_max_age_hours=self.settings.summary_freshness_hours,
            snapshot_max_age_minutes=self.settings.snapshot_freshness_minutes,
        )
        articles = [
            {
                "id": article.id,
                "title": article.title,
                "source": article.source_id,
                "published_at": article.published_at,
                "related_tickers": article.related_tickers,
            }
            for article in context.articles
        ]
        market_context = [
            {
                "symbol": snapshot.symbol,
                "price": snapshot.price,
                "percent_change": snapshot.percent_change,
                "indicators": snapshot.indicators,
            }
            for snapshot in context.market_snapshots
        ]
        recap = build_brief_response(
            articles=articles,
            summaries=[summary.text for summary in context.summaries],
            market_context=market_context,
            local_region=user.local_region,
        )
        if not articles and not market_context:
            return "Daily recap: no fresh news or market snapshots are available right now."
        return f"Daily recap:\n\n{recap}"


async def run_scheduler_tick(settings: Settings, last_refresh_at: datetime | None) -> datetime:
    control = SchedulerControlService(settings)
    memory_service = MemoryConsolidationService(control.session_factory, settings)
    now = datetime.now(UTC)
    refresh_due = last_refresh_at is None or (
        now - last_refresh_at
    ) >= timedelta(seconds=settings.news_fetch_interval_seconds)
    if refresh_due and await control.can_start_refresh():
        await control.run_refresh(job_type="news_refresh")
        last_refresh_at = now
    recap_service = DailyRecapService(settings)
    await recap_service.send_due_recaps(now=now)
    await memory_service.process_due_jobs()
    await control.cleanup_expired_content()
    return last_refresh_at or now
