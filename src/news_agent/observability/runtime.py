from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import Bot
from telegram.error import TelegramError

from news_agent.settings import Settings
from news_agent.storage.repositories import (
    ConversationEventRepository,
    RuntimeAlertRepository,
    RuntimeErrorRepository,
    RuntimeRunRepository,
    RuntimeStepRepository,
)

logger = logging.getLogger(__name__)


class RuntimeTraceService:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def ensure_run(
        self,
        *,
        workflow: str,
        trigger: str | None,
        telegram_user_id: int | None = None,
        chat_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        run_id: int | None = None,
    ) -> int:
        if run_id:
            return run_id
        async with self.session_factory() as session:
            item = await RuntimeRunRepository(session).start(
                workflow=workflow,
                trigger=trigger,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                metadata=metadata,
            )
            await session.commit()
            return item.id

    async def start_step(
        self,
        *,
        run_id: int,
        workflow: str,
        step_name: str,
        step_type: str,
        parent_step_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        async with self.session_factory() as session:
            item = await RuntimeStepRepository(session).start(
                run_id=run_id,
                workflow=workflow,
                step_name=step_name,
                step_type=step_type,
                parent_step_id=parent_step_id,
                metadata=metadata,
            )
            await session.commit()
            return item.id

    async def finish_step(
        self,
        step_id: int,
        *,
        status: str,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self.session_factory() as session:
            await RuntimeStepRepository(session).finish(
                step_id,
                status=status,
                error_message=error_message,
                metadata=metadata,
            )
            await session.commit()

    async def record_error(
        self,
        *,
        run_id: int,
        workflow: str,
        step_name: str,
        error_message: str,
        step_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        async with self.session_factory() as session:
            item = await RuntimeErrorRepository(session).create(
                run_id=run_id,
                workflow=workflow,
                step_name=step_name,
                error_message=error_message,
                step_id=step_id,
                metadata=metadata,
            )
            await session.commit()
            return item.id

    async def finish_run(self, run_id: int, *, status: str, summary: str | None = None) -> None:
        async with self.session_factory() as session:
            await RuntimeRunRepository(session).finish(run_id, status=status, summary=summary)
            await session.commit()

    async def update_run_metadata(self, run_id: int, metadata: dict[str, Any]) -> None:
        async with self.session_factory() as session:
            await RuntimeRunRepository(session).update_metadata(run_id, metadata)
            await session.commit()


class RuntimeAlertService:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token else None

    async def send_alert(
        self,
        *,
        run_id: int,
        message_text: str,
        error_id: int | None = None,
    ) -> None:
        target = str(self.settings.runtime_alert_telegram_chat_id or "")
        delivered_at: datetime | None = None
        status = "skipped"
        if self.bot and self.settings.runtime_alert_telegram_chat_id:
            try:
                await self.bot.send_message(
                    chat_id=self.settings.runtime_alert_telegram_chat_id,
                    text=message_text,
                )
            except TelegramError as exc:
                logger.warning(
                    "runtime alert delivery failed target=%s error=%s",
                    target,
                    exc,
                )
                status = "failed"
            else:
                delivered_at = datetime.now(UTC)
                status = "delivered"

        async with self.session_factory() as session:
            await RuntimeAlertRepository(session).create(
                run_id=run_id,
                error_id=error_id,
                channel="telegram",
                status=status,
                message_text=message_text,
                target=target or None,
                delivered_at=delivered_at,
            )
            await session.commit()


class RefreshReportService:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token else None

    async def record_and_deliver(
        self,
        *,
        run_id: int,
        status: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        report = await self._build_report(run_id=run_id, status=status, state=state)
        delivery_status = "disabled"
        target_chat_id: int | None = None
        delivered_at: datetime | None = None

        if self.settings.refresh_report_enabled:
            async with self.session_factory() as session:
                target_chat_id = await ConversationEventRepository(session).latest_user_chat_id()
            if target_chat_id is None:
                delivery_status = "skipped_no_chat"
            elif self.bot is None:
                delivery_status = "skipped_no_bot"
            else:
                try:
                    await self.bot.send_message(chat_id=target_chat_id, text=report["text"])
                except TelegramError as exc:
                    logger.warning(
                        "refresh report delivery failed target=%s error=%s",
                        target_chat_id,
                        exc,
                    )
                    delivery_status = "failed"
                    report["delivery_error"] = str(exc)
                else:
                    delivery_status = "delivered"
                    delivered_at = datetime.now(UTC)

        report.update(
            {
                "delivery_status": delivery_status,
                "target_chat_id": target_chat_id,
                "delivered_at": delivered_at.isoformat() if delivered_at else None,
            }
        )
        async with self.session_factory() as session:
            await RuntimeRunRepository(session).update_metadata(
                run_id,
                {
                    "refresh_report": report,
                    "report_delivery_status": delivery_status,
                },
            )
            await session.commit()
        return report

    async def _build_report(
        self,
        *,
        run_id: int,
        status: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = dict(state.get("metadata") or {})
        fetch_metrics = dict(metadata.get("fetch_metrics") or {})
        errors = list(state.get("errors", []))
        duration_seconds: float | None = None
        async with self.session_factory() as session:
            run = await RuntimeRunRepository(session).get(run_id)
            if run is not None:
                started_at = run.started_at
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                duration_seconds = max((datetime.now(UTC) - started_at).total_seconds(), 0)

        source_metrics = dict(fetch_metrics.get("sources") or {})
        ticker_metrics = dict(fetch_metrics.get("tickers") or {})
        retry_count = int(fetch_metrics.get("retry_count", 0) or 0)
        first_failure = errors[0] if errors else "none"
        saved = int(metadata.get("saved_article_count", 0) or 0)
        accepted = int(metadata.get("accepted_article_count", 0) or 0)
        rejected = int(metadata.get("rejected_article_count", 0) or 0)
        duplicates = int(metadata.get("duplicate_article_count", 0) or 0)
        fetched = int(source_metrics.get("items_fetched", 0) or 0)
        snapshots = int(metadata.get("market_snapshot_count", 0) or 0)
        sources_attempted = int(source_metrics.get("attempted", 0) or 0)
        sources_succeeded = int(source_metrics.get("succeeded", 0) or 0)
        sources_failed = int(source_metrics.get("failed", 0) or 0)

        duration_text = "n/a" if duration_seconds is None else f"{duration_seconds:.1f}s"
        text = "\n".join(
            [
                f"Refresh report: run {run_id}",
                f"- Status: {status}",
                f"- Duration: {duration_text}",
                (
                    f"- Sources: {sources_succeeded}/{sources_attempted} succeeded, "
                    f"{sources_failed} failed"
                ),
                (
                    f"- Articles: {fetched} fetched, {accepted} accepted, "
                    f"{saved} saved, {rejected} rejected, {duplicates} duplicates"
                ),
                f"- Market snapshots: {snapshots}",
                f"- Retries: {retry_count}",
                f"- Source health: {_source_health_text(source_metrics)}",
                f"- Failures: {first_failure}",
                f"- Debug: /trace {run_id} or /job {run_id}",
            ]
        )
        return {
            "run_id": run_id,
            "status": status,
            "duration_seconds": duration_seconds,
            "sources": source_metrics,
            "tickers": ticker_metrics,
            "articles": {
                "fetched": fetched,
                "accepted": accepted,
                "saved": saved,
                "rejected": rejected,
                "duplicates": duplicates,
            },
            "market_snapshots": snapshots,
            "retry_count": retry_count,
            "failures": errors,
            "text": text,
        }


def _source_health_text(source_metrics: dict[str, Any]) -> str:
    health = source_metrics.get("health")
    if not isinstance(health, dict) or not health:
        return "n/a"
    counts: dict[str, int] = {}
    for status in health.values():
        key = str(status or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{status} {count}" for status, count in sorted(counts.items()))


def summarize_run_state(workflow: str, state: dict[str, Any]) -> str:
    errors = list(state.get("errors", []))
    if errors:
        return f"{workflow} completed with {len(errors)} error(s)"
    if workflow == "chat":
        response = str(state.get("final_response") or state.get("response") or "")
        return f"chat completed with {len(response)} response chars"
    if workflow in {"manual_refresh", "news_refresh", "scheduler", "market_research_refresh"}:
        saved = state.get("metadata", {}).get("saved_article_count", 0)
        return f"{workflow} completed with {saved} saved articles"
    return f"{workflow} completed"
