import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import Bot

from news_agent.bot.handlers import split_telegram_message
from news_agent.harness.contracts import HarnessRequest
from news_agent.settings import Settings
from news_agent.storage.repositories import (
    ConversationEventRepository,
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
        from news_agent.harness.service import StockNewsHarness

        harness = StockNewsHarness(self.session_factory, self.settings)
        context = harness.context(
            principal="daily-research-report", profile="scheduled",
            data={"report_date": report_date},
        ).model_copy(update={"as_of": now})
        result = await harness.run(
            HarnessRequest(operation="research.report", profile="scheduled"), context,
        )
        if not result.ok:
            raise RuntimeError(f"Daily research report unavailable: {result.status}")
        return result.data["response"]
