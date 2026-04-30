import asyncio
import logging

from telegram.ext import Application

from news_agent.bot.handlers import register_handlers
from news_agent.graph.chat_graph import build_chat_graph
from news_agent.logging import configure_logging
from news_agent.settings import get_settings
from news_agent.storage.database import create_session_factory

logger = logging.getLogger(__name__)


async def run_bot() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    session_factory = create_session_factory(settings)
    chat_graph = build_chat_graph(session_factory, settings)

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["chat_graph"] = chat_graph
    register_handlers(application)

    logger.info("starting news agent bot")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
