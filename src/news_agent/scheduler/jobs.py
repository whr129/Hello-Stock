import asyncio
import logging

from news_agent.graph.scheduler_graph import build_scheduler_graph
from news_agent.logging import configure_logging
from news_agent.settings import Settings, get_settings
from news_agent.storage.database import create_session_factory

logger = logging.getLogger(__name__)


async def run_scheduler_once(job_type: str = "manual", settings: Settings | None = None) -> dict:
    config = settings or get_settings()
    session_factory = create_session_factory(config)
    graph = build_scheduler_graph(session_factory, config)
    logger.info("scheduler run starting", extra={"job_type": job_type})
    result = await graph.ainvoke({"job_type": job_type, "errors": [], "metadata": {}})
    logger.info(
        "scheduler run completed",
        extra={
            "job_type": job_type,
            "error_count": len(result.get("errors", [])),
            "summary_count": len(result.get("summaries", [])),
            "saved_article_count": result.get("metadata", {}).get("saved_article_count", 0),
        },
    )
    return result


async def scheduler_loop(settings: Settings) -> None:
    logger.info(
        "scheduler loop starting",
        extra={"interval_seconds": settings.news_fetch_interval_seconds},
    )
    while True:
        await run_scheduler_once("news_refresh", settings)
        logger.info(
            "scheduler sleeping",
            extra={"interval_seconds": settings.news_fetch_interval_seconds},
        )
        await asyncio.sleep(settings.news_fetch_interval_seconds)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(scheduler_loop(settings))
