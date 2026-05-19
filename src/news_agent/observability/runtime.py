from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import Bot
from telegram.error import TelegramError

from news_agent.settings import Settings
from news_agent.storage.repositories import (
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


def summarize_run_state(workflow: str, state: dict[str, Any]) -> str:
    errors = list(state.get("errors", []))
    if errors:
        return f"{workflow} completed with {len(errors)} error(s)"
    if workflow == "chat":
        response = str(state.get("final_response") or state.get("response") or "")
        return f"chat completed with {len(response)} response chars"
    if workflow in {"manual_refresh", "news_refresh", "scheduler"}:
        saved = state.get("metadata", {}).get("saved_article_count", 0)
        return f"{workflow} completed with {saved} saved articles"
    return f"{workflow} completed"
