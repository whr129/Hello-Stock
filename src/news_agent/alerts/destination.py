"""Reuse the daily research report's destination unless alerts override it."""

from typing import Any

from news_agent.settings import Settings
from news_agent.storage.repositories import ConversationEventRepository


async def resolve_alert_destination(factory: Any, settings: Settings) -> int:
    configured = settings.market_alert_chat_id or settings.daily_research_report_chat_id
    if configured:
        return configured
    async with factory() as session:
        return await ConversationEventRepository(session).latest_user_chat_id() or 0
