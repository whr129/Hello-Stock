import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from time import monotonic
from typing import Any

from news_agent.graph.scheduler_graph import build_scheduler_graph
from news_agent.logging import configure_logging
from news_agent.observability.runtime import RuntimeTraceService
from news_agent.scheduler.service import run_scheduler_tick
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
    from news_agent.alerts.config import resolve_alert_settings

    alert_settings = resolve_alert_settings(settings)
    if not alert_settings.market_alerts_enabled:
        await _bulk_scheduler_loop(settings)
        return
    from news_agent.alerts.collector import FastNewsCollector
    from news_agent.alerts.delivery import MarketAlertDispatcher
    from news_agent.alerts.service import MarketAlertService

    factory = create_session_factory(alert_settings)
    service = MarketAlertService(factory, alert_settings)
    alert_settings = service.settings
    collector = FastNewsCollector(factory, alert_settings)
    dispatcher = MarketAlertDispatcher(factory, alert_settings)
    trace = RuntimeTraceService(factory, alert_settings)
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(_bulk_scheduler_loop(settings), name="bulk-research")
            tasks.create_task(
                _market_periodic(
                    "market_alert_collect", collector.collect_once,
                    alert_settings.market_alert_poll_seconds, trace,
                ), name="market-alert-collect",
            )
            tasks.create_task(
                _market_periodic(
                    "market_alert_assess", service.process_once,
                    alert_settings.market_alert_dispatch_seconds, trace,
                ), name="market-alert-assess",
            )
            tasks.create_task(
                _market_periodic(
                    "market_alert_deliver", dispatcher.dispatch_once,
                    alert_settings.market_alert_dispatch_seconds, trace,
                ), name="market-alert-deliver",
            )
    finally:
        await service.aclose()
        await factory.kw["bind"].dispose()


async def _market_periodic(
    workflow: str, operation: Callable[[], Awaitable[Any]], interval: float,
    trace: RuntimeTraceService,
) -> None:
    while True:
        started = monotonic()
        run_id = None
        try:
            run_id = await trace.ensure_run(workflow=workflow, trigger="scheduler")
            counts = await operation()
            failed = isinstance(counts, dict) and counts.get("failed")
            status = "completed_with_errors" if failed else "completed"
            await trace.finish_run(run_id, status=status, summary=str(counts)[:500])
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Exceptions can carry credential-bearing URLs; record only their type.
            logger.error(
                "scheduler task failed workflow=%s type=%s", workflow, type(error).__name__
            )
            if run_id is not None:
                try:
                    await trace.record_error(
                        run_id=run_id, workflow=workflow, step_name=workflow,
                        error_message=type(error).__name__,
                    )
                    await trace.finish_run(run_id, status="failed", summary=type(error).__name__)
                except Exception:
                    logger.error("scheduler task trace unavailable workflow=%s", workflow)
        await asyncio.sleep(max(interval - (monotonic() - started), 0.1))


async def _bulk_scheduler_loop(settings: Settings) -> None:
    logger.info(
        "scheduler loop starting",
        extra={"tick_seconds": settings.scheduler_tick_seconds},
    )
    last_refresh_at: dict[str, datetime] | None = None
    while True:
        try:
            last_refresh_at = await run_scheduler_tick(settings, last_refresh_at)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("bulk scheduler tick failed type=%s", type(error).__name__)
        logger.info(
            "scheduler sleeping",
            extra={"interval_seconds": settings.scheduler_tick_seconds},
        )
        await asyncio.sleep(settings.scheduler_tick_seconds)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(scheduler_loop(settings))
