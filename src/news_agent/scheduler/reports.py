import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import Bot

from news_agent.agent.guardrails import enforce_financial_guardrails
from news_agent.bot.handlers import split_telegram_message
from news_agent.research.analysis import explain_candidates, visible_candidate_explanations
from news_agent.research.link_validation import validate_candidate_links
from news_agent.research.reporting import format_candidates
from news_agent.settings import Settings
from news_agent.storage.repositories import (
    ConversationEventRepository,
    MarketSignalRepository,
    RuntimeRunRepository,
)

logger = logging.getLogger(__name__)


class DailyResearchReportService:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def deliver_if_due(self, now: datetime) -> None:
        if not self.settings.daily_research_report_enabled or not self.settings.telegram_bot_token:
            return
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        local_now = now.astimezone(ZoneInfo(self.settings.daily_research_report_timezone))
        if local_now.strftime("%H:%M") < self.settings.daily_research_report_time:
            return

        # Keep the lock in a separate transaction while delivery progress is committed.
        try:
            async with self.session_factory() as lock_session:
                if await RuntimeRunRepository(lock_session).lock_daily_research_report():
                    await self._deliver(local_now.date().isoformat(), now)
        except Exception:
            # A report failure must not stop ingestion or memory processing.
            logger.exception("daily research report failed")

    async def _deliver(self, report_date: str, now: datetime) -> None:
        async with self.session_factory() as session:
            repository = RuntimeRunRepository(session)
            run = await repository.get_daily_research_report(report_date)
            if run is not None:
                metadata = dict(run.run_metadata)
                if run.status == "completed":
                    return
                if metadata.get("attempts", 0) >= self.settings.daily_research_report_max_attempts:
                    return
                last_attempt = datetime.fromisoformat(metadata["last_attempt_at"])
                if (now - last_attempt).total_seconds() < (
                    self.settings.daily_research_report_retry_seconds
                ):
                    return
            else:
                chat_id = self.settings.daily_research_report_chat_id or (
                    await ConversationEventRepository(session).latest_user_chat_id()
                )
                if chat_id is None:
                    logger.info("daily research report skipped: no destination chat")
                    return
                metadata = {
                    "report_date": report_date,
                    "timezone": self.settings.daily_research_report_timezone,
                    "target_chat_id": chat_id,
                    "attempts": 0,
                    "chunks_sent": 0,
                }
                run = await repository.start(
                    workflow="daily_research_report",
                    trigger=report_date,
                    chat_id=chat_id,
                    metadata=metadata,
                )

            run_id = run.id
            metadata.update(
                attempts=metadata["attempts"] + 1,
                last_attempt_at=now.isoformat(),
                delivery_status="sending",
            )
            await repository.update_metadata(run_id, metadata)
            await session.commit()
            try:
                if not metadata.get("text"):
                    metadata["text"] = await self._build_report(report_date, now)
                    await repository.update_metadata(run_id, metadata)
                    await session.commit()
                chunks = split_telegram_message(metadata["text"])
                async with Bot(token=self.settings.telegram_bot_token) as bot:
                    for index in range(metadata["chunks_sent"], len(chunks)):
                        await bot.send_message(
                            chat_id=metadata["target_chat_id"], text=chunks[index]
                        )
                        metadata["chunks_sent"] = index + 1
                        await repository.update_metadata(run_id, metadata)
                        await session.commit()
            except Exception as exc:
                await session.rollback()
                metadata.update(delivery_status="failed", delivery_error=str(exc))
                await repository.update_metadata(run_id, metadata)
                await repository.finish(run_id, status="failed", summary="Daily report failed")
                await session.commit()
                logger.warning("daily research report delivery failed: %s", exc)
                return

            metadata.update(delivery_status="delivered", delivered_at=datetime.now(UTC).isoformat())
            metadata["delivery_error"] = None
            await repository.update_metadata(run_id, metadata)
            await repository.finish(
                run_id, status="completed", summary=f"Daily research report for {report_date}"
            )
            await session.commit()

    async def _build_report(self, report_date: str, now: datetime) -> str:
        limit = self.settings.daily_research_report_max_candidates
        async with self.session_factory() as session:
            repository = MarketSignalRepository(session)
            snapshots = await repository.fetch_top_candidates(
                window="24h", limit=limit * 3, since=now - timedelta(hours=24)
            )
            explanations = await validate_candidate_links(
                explain_candidates(
                    snapshots,
                    min_strong_sources=self.settings.signal_min_strong_evidence_sources,
                ),
                recheck_hours=self.settings.evidence_link_recheck_hours,
            )
            for explanation in explanations:
                if explanation.snapshot_id is not None:
                    await repository.update_snapshot_evidence(
                        explanation.snapshot_id, explanation.evidence
                    )
            await session.commit()
        candidates = visible_candidate_explanations(
            explanations,
            limit=limit,
            include_developing=self.settings.signal_allow_developing_default,
        )
        report = format_candidates(
            candidates, max_evidence_items=self.settings.research_report_max_evidence_items
        )
        return (
            f"Daily research report — {report_date}\n"
            f"Timezone: {self.settings.daily_research_report_timezone}\n"
            "Latest stored 24-hour signals updated within the past day.\n\n"
            f"{enforce_financial_guardrails(report)}"
        )
