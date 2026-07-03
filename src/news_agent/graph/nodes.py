import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.graph.state import SchedulerState
from news_agent.ingestion.dedupe import content_hash
from news_agent.ingestion.market_impact import MarketImpactClassifier
from news_agent.ingestion.providers import IngestProviderRegistry
from news_agent.markets.yahoo import YahooMarketDataProvider
from news_agent.memory.embeddings import EmbeddingService
from news_agent.observability.runtime import (
    RefreshReportService,
    RuntimeAlertService,
    RuntimeTraceService,
    summarize_run_state,
)
from news_agent.research.scheduler import (
    backfill_signal_evidence_links,
    count_confident_signal_context,
    enrich_market_sectors,
    extract_market_mentions,
    prune_market_research_data,
    score_market_signals,
)
from news_agent.settings import Settings
from news_agent.storage.models import JobRun, Source
from news_agent.storage.repositories import (
    ArticleRepository,
    EmbeddingRepository,
    JobRepository,
    MarketEntityRepository,
    MarketMentionRepository,
    MarketRepository,
    SourceRepository,
    SummaryRepository,
)
from news_agent.summarizer.service import Summarizer, SummaryRequest

logger = logging.getLogger(__name__)
DEFAULT_SOURCE_PACK_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "market-research" / "default-sources.json"
)


def _source_dict_to_model(payload: dict[str, Any]) -> Source:
    return Source(
        id=payload["id"],
        owner_user_id=payload.get("owner_user_id"),
        name=payload["name"],
        url=payload["url"],
        provider=payload["provider"],
        external_account=payload["external_account"],
        config=dict(payload.get("config") or {}),
        field_mapping=dict(payload.get("field_mapping") or {}),
        fetch_mode=payload.get("fetch_mode"),
        category=payload["category"],
        enabled=payload.get("enabled", True),
        trust_score=payload.get("trust_score", 0.5),
    )


class SchedulerNodes:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.market_provider = YahooMarketDataProvider()
        self.summarizer = Summarizer(settings)
        self.embedding_service = EmbeddingService(settings)
        self.ingest_registry = IngestProviderRegistry(settings)
        self.market_impact_classifier = MarketImpactClassifier(settings)
        self.trace_service = RuntimeTraceService(session_factory, settings)
        self.alert_service = RuntimeAlertService(session_factory, settings)
        self.report_service = RefreshReportService(session_factory, settings)

    def traced(self, step_name: str, func):
        async def wrapped(state: SchedulerState) -> SchedulerState:
            workflow = state.get("job_type", "scheduler")
            run_id = await self.trace_service.ensure_run(
                workflow=workflow,
                trigger=workflow,
                metadata={"job_id": state.get("job_id", 0)},
                run_id=state.get("runtime_run_id"),
            )
            parent_step_id = state.get("active_step_id")
            step_id = await self.trace_service.start_step(
                run_id=run_id,
                workflow=workflow,
                step_name=step_name,
                step_type="node",
                parent_step_id=parent_step_id,
                metadata={"job_id": state.get("job_id", 0)},
            )
            state = {**state, "runtime_run_id": run_id, "active_step_id": step_id}
            try:
                result = await func(state)
            except Exception as exc:
                message = str(exc)
                await self.trace_service.finish_step(
                    step_id,
                    status="failed",
                    error_message=message,
                )
                error_id = await self.trace_service.record_error(
                    run_id=run_id,
                    workflow=workflow,
                    step_name=step_name,
                    error_message=message,
                    step_id=step_id,
                    metadata={"job_id": state.get("job_id", 0)},
                )
                await self.trace_service.finish_run(run_id, status="failed", summary=message[:500])
                if _is_refresh_workflow(workflow):
                    failed_state = {
                        **state,
                        "runtime_run_id": run_id,
                        "errors": [*list(state.get("errors", [])), message],
                    }
                    await self.report_service.record_and_deliver(
                        run_id=run_id,
                        status="failed",
                        state=failed_state,
                    )
                await self.alert_service.send_alert(
                    run_id=run_id,
                    error_id=error_id,
                    message_text=(
                        f"Runtime alert\n"
                        f"- Workflow: {workflow}\n"
                        f"- Run: {run_id}\n"
                        f"- Step: {step_name}\n"
                        f"- Error: {message}"
                    ),
                )
                raise

            result = {**result, "runtime_run_id": run_id, "active_step_id": parent_step_id}
            await self.trace_service.finish_step(step_id, status="completed")
            if step_name == "retry_or_recover":
                status = "completed_with_errors" if result.get("errors") else "completed"
                await self.trace_service.finish_run(
                    run_id,
                    status=status,
                    summary=summarize_run_state(workflow, result),
                )
                if _is_refresh_workflow(workflow):
                    await self.report_service.record_and_deliver(
                        run_id=run_id,
                        status=status,
                        state=result,
                    )
                if result.get("errors"):
                    await self.alert_service.send_alert(
                        run_id=run_id,
                        message_text=(
                            f"Runtime alert\n"
                            f"- Workflow: {workflow}\n"
                            f"- Run: {run_id}\n"
                            f"- Step: {step_name}\n"
                            f"- Errors: {len(result['errors'])}\n"
                            f"- First error: {result['errors'][0]}"
                        ),
                    )
            return result

        return wrapped

    async def _run_blocking_with_timeout(self, label: str, func, timeout_seconds: int):
        started_at = perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(func),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            elapsed = perf_counter() - started_at
            logger.warning(
                "scheduler timed out %s after %.2fs timeout=%ss",
                label,
                elapsed,
                timeout_seconds,
            )
            raise TimeoutError(f"{label} timed out after {timeout_seconds}s") from exc

        elapsed = perf_counter() - started_at
        logger.info("scheduler finished %s in %.2fs", label, elapsed)
        return result

    async def _run_with_retries(
        self,
        *,
        label: str,
        func,
        timeout_seconds: int,
        max_attempts: int,
        backoff_seconds: int,
        attempts: list[dict[str, Any]],
    ):
        max_attempts = max(max_attempts, 1)
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            started_at = perf_counter()
            try:
                result = await self._run_blocking_with_timeout(
                    label=f"{label} attempt={attempt}",
                    func=func,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                elapsed_ms = max(int((perf_counter() - started_at) * 1000), 0)
                last_error = exc
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "failed",
                        "duration_ms": elapsed_ms,
                        "error": str(exc),
                    }
                )
                logger.warning(
                    "scheduler provider attempt failed "
                    "label=%s attempt=%s max_attempts=%s error=%s",
                    label,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    sleep_seconds = max(backoff_seconds, 0) * attempt
                    attempts[-1]["backoff_seconds"] = sleep_seconds
                    if sleep_seconds:
                        await asyncio.sleep(sleep_seconds)
                continue

            elapsed_ms = max(int((perf_counter() - started_at) * 1000), 0)
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "completed",
                    "duration_ms": elapsed_ms,
                }
            )
            return result

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{label} failed without an exception")

    async def load_due_sources(self, state: SchedulerState) -> SchedulerState:
        job_type = state.get("job_type", "market_research_refresh")
        pipeline_scope = _pipeline_scope(job_type, state.get("pipeline_scope"))
        logger.info(
            "scheduler loading due work",
            extra={"job_type": job_type, "pipeline_scope": pipeline_scope},
        )
        async with self.session_factory() as session:
            source_repo = SourceRepository(session)
            default_sources = _default_sources_from_settings(self.settings)
            if default_sources:
                logger.info(
                    "scheduler ensuring configured default sources",
                    extra={"source_count": len(default_sources)},
                )
                await source_repo.ensure_default_sources(default_sources)
                await session.commit()
            sources = await source_repo.list_all_enabled()
            enabled_source_count = len(sources)
            if pipeline_scope == "market_prices":
                sources = []
            elif pipeline_scope in {"breaking_resources", "daily_resources"}:
                sources = [
                    source
                    for source in sources
                    if _source_in_pipeline(source, pipeline_scope)
                    and _source_is_due(source, self.settings)
                ]
            else:
                sources = [source for source in sources if _source_is_due(source, self.settings)]
            tickers = (
                await self._market_universe_symbols(session)
                if pipeline_scope in {"all", "market_prices"}
                else []
            )
            job = await JobRepository(session).start(job_type)
            await session.commit()

        due_sources = [
            {
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "provider": source.provider,
                "external_account": source.external_account,
                "config": dict(source.config or {}),
                "field_mapping": dict(source.field_mapping or {}),
                "fetch_mode": source.fetch_mode,
                "enabled": source.enabled,
                "trust_score": source.trust_score,
                "last_fetched_at": source.last_fetched_at,
                "last_success_at": source.last_success_at,
                "last_error": source.last_error,
                "category": source.category,
            }
            for source in sources
        ]
        logger.info(
            "scheduler loaded due work",
            extra={
                "job_id": job.id,
                "source_count": len(due_sources),
                "ticker_count": len(tickers),
            },
        )
        return {
            **state,
            "job_id": job.id,
            "due_sources": due_sources,
            "due_tickers": tickers,
            "errors": state.get("errors", []),
            "metadata": {
                **state.get("metadata", {}),
                "pipeline_scope": pipeline_scope,
                "fetch_metrics": {
                    "sources": {
                        "enabled": enabled_source_count,
                        "due": len(due_sources),
                        "skipped": max(enabled_source_count - len(due_sources), 0),
                    },
                    "tickers": {"due": len(tickers)},
                    "retry_count": 0,
                    "failures": [],
                },
            },
        }

    async def _market_universe_symbols(self, session) -> list[str]:
        configured = _parse_symbol_csv(self.settings.market_universe_symbols)
        entities = [
            entity.ticker
            for entity in await MarketEntityRepository(session).list_active()
            if entity.ticker
        ]
        mentioned = await MarketMentionRepository(session).top_tickers(
            since=datetime.now(UTC) - timedelta(days=7),
            limit=25,
        )
        return sorted(dict.fromkeys(configured + entities + mentioned))

    async def fetch_parallel(self, state: SchedulerState) -> SchedulerState:
        fetched_articles: list[dict[str, Any]] = []
        errors = list(state.get("errors", []))
        provider_counts: dict[str, int] = {}
        metadata = dict(state.get("metadata") or {})
        fetch_metrics = _ensure_fetch_metrics(metadata)
        source_metrics = fetch_metrics["sources"]
        ticker_metrics = fetch_metrics["tickers"]
        source_metrics["attempted"] = 0
        source_metrics["succeeded"] = 0
        source_metrics["failed"] = 0
        source_metrics["items_fetched"] = 0
        source_metrics["health"] = dict(source_metrics.get("health") or {})
        ticker_metrics["attempted"] = 0
        ticker_metrics["succeeded"] = 0
        ticker_metrics["failed"] = 0
        logger.info(
            "scheduler fetching feeds",
            extra={"source_count": len(state.get("due_sources", []))},
        )

        for source in state.get("due_sources", []):
            provider_step_id: int | None = None
            source_metrics["attempted"] += 1
            attempts: list[dict[str, Any]] = []
            try:
                provider_step_id = await self.trace_service.start_step(
                    run_id=state["runtime_run_id"],
                    workflow=state.get("job_type", "scheduler"),
                    step_name=f"source:{source['name']}",
                    step_type="provider",
                    parent_step_id=state.get("active_step_id"),
                    metadata={"provider": source["provider"], "source_id": source["id"]},
                )
                logger.info(
                    "scheduler fetching feed source=%s url=%s timeout=%ss",
                    source["name"],
                    source["url"],
                    self.settings.rss_fetch_timeout_seconds,
                )
                provider = self.ingest_registry.get(source["provider"])
                source_payload = dict(source)
                articles = await self._run_with_retries(
                    label=f"source source={source['name']}",
                    attempts=attempts,
                    max_attempts=self.settings.source_fetch_max_attempts,
                    backoff_seconds=self.settings.source_fetch_retry_backoff_seconds,
                    timeout_seconds=self.settings.rss_fetch_timeout_seconds + 2,
                    func=lambda payload=source_payload, provider=provider: provider.fetch_items(
                        _source_dict_to_model(payload),
                        timeout_seconds=self.settings.rss_fetch_timeout_seconds,
                    ),
                )
                articles = _limit_articles_for_source(source, articles, self.settings)
                articles = _exclude_items_for_source(source, articles, self.settings)
                retry_count = max(len(attempts) - 1, 0)
                fetch_metrics["retry_count"] += retry_count
                source_metrics["succeeded"] += 1
                source_metrics["items_fetched"] += len(articles)
                source_metrics["health"][source["name"]] = "empty" if not articles else "healthy"
                provider_counts[source["provider"]] = provider_counts.get(
                    source["provider"],
                    0,
                ) + len(articles)
                await self.trace_service.finish_step(
                    provider_step_id,
                    status="completed",
                    metadata={
                        "article_count": len(articles),
                        "attempts": attempts,
                        "retry_count": retry_count,
                    },
                )
                logger.info(
                    "scheduler fetched source source=%s articles=%s",
                    source["name"],
                    len(articles),
                )
                for article in articles:
                    fetched_articles.append(
                        {
                            "source_id": source["id"],
                            "source_name": source["name"],
                            "provider": source["provider"],
                            "category": source["category"],
                            "title": article.title,
                            "url": article.url,
                            "published_at": article.published_at,
                            "summary": article.body_text,
                            "author": article.author,
                            "provider_metadata": dict(article.metadata or {}),
                        }
                    )
                async with self.session_factory() as session:
                    await SourceRepository(session).mark_fetch_result(
                        source["id"],
                        fetched_at=datetime.now(UTC),
                        success=True,
                    )
                    await session.commit()
            except Exception as exc:
                retry_count = max(len(attempts) - 1, 0)
                fetch_metrics["retry_count"] += retry_count
                source_metrics["failed"] += 1
                failure = {
                    "kind": "source",
                    "name": source["name"],
                    "provider": source["provider"],
                    "attempt_count": len(attempts),
                    "error": str(exc),
                }
                fetch_metrics["failures"].append(failure)
                source_metrics["health"][source["name"]] = "failing"
                if provider_step_id is not None:
                    await self.trace_service.finish_step(
                        provider_step_id,
                        status="failed",
                        error_message=str(exc),
                        metadata={"attempts": attempts, "retry_count": retry_count},
                    )
                    await self.trace_service.record_error(
                        run_id=state["runtime_run_id"],
                        workflow=state.get("job_type", "scheduler"),
                        step_name=f"source:{source['name']}",
                        error_message=str(exc),
                        step_id=provider_step_id,
                        metadata={"provider": source["provider"], "source_id": source["id"]},
                    )
                logger.warning(
                    "scheduler source fetch failed source=%s error=%s",
                    source["name"],
                    exc,
                )
                errors.append(f"{source['name']}: {exc}")
                async with self.session_factory() as session:
                    await SourceRepository(session).mark_fetch_result(
                        source["id"],
                        fetched_at=datetime.now(UTC),
                        success=False,
                        error=str(exc),
                    )
                    await session.commit()

        market_snapshots: list[dict[str, Any]] = []
        logger.info(
            "scheduler fetching market snapshots",
            extra={"ticker_count": len(state.get("due_tickers", []))},
        )
        for ticker in state.get("due_tickers", []):
            provider_step_id: int | None = None
            ticker_metrics["attempted"] += 1
            attempts: list[dict[str, Any]] = []
            try:
                provider_step_id = await self.trace_service.start_step(
                    run_id=state["runtime_run_id"],
                    workflow=state.get("job_type", "scheduler"),
                    step_name=f"ticker:{ticker}",
                    step_type="provider",
                    parent_step_id=state.get("active_step_id"),
                    metadata={"ticker": ticker},
                )
                logger.info(
                    "scheduler fetching ticker ticker=%s timeout=%ss",
                    ticker,
                    self.settings.market_fetch_timeout_seconds,
                )
                ticker_symbol = ticker
                snapshot = await self._run_with_retries(
                    label=f"ticker ticker={ticker}",
                    attempts=attempts,
                    max_attempts=self.settings.market_fetch_max_attempts,
                    backoff_seconds=self.settings.market_fetch_retry_backoff_seconds,
                    timeout_seconds=self.settings.market_fetch_timeout_seconds,
                    func=lambda symbol=ticker_symbol: self.market_provider.get_snapshot(symbol),
                )
                retry_count = max(len(attempts) - 1, 0)
                fetch_metrics["retry_count"] += retry_count
                ticker_metrics["succeeded"] += 1
                market_snapshots.append(
                    {
                        "symbol": snapshot.symbol,
                        "price": snapshot.price,
                        "percent_change": snapshot.percent_change,
                        "indicators": snapshot.indicators,
                    }
                )
                await self.trace_service.finish_step(
                    provider_step_id,
                    status="completed",
                    metadata={
                        "symbol": snapshot.symbol,
                        "attempts": attempts,
                        "retry_count": retry_count,
                    },
                )
                logger.info(
                    "scheduler fetched ticker ticker=%s price=%s percent_change=%s",
                    snapshot.symbol,
                    snapshot.price,
                    snapshot.percent_change,
                )
            except Exception as exc:
                retry_count = max(len(attempts) - 1, 0)
                fetch_metrics["retry_count"] += retry_count
                ticker_metrics["failed"] += 1
                failure = {
                    "kind": "ticker",
                    "name": ticker,
                    "attempt_count": len(attempts),
                    "error": str(exc),
                }
                fetch_metrics["failures"].append(failure)
                if provider_step_id is not None:
                    await self.trace_service.finish_step(
                        provider_step_id,
                        status="failed",
                        error_message=str(exc),
                        metadata={"attempts": attempts, "retry_count": retry_count},
                    )
                    await self.trace_service.record_error(
                        run_id=state["runtime_run_id"],
                        workflow=state.get("job_type", "scheduler"),
                        step_name=f"ticker:{ticker}",
                        error_message=str(exc),
                        step_id=provider_step_id,
                        metadata={"ticker": ticker},
                    )
                logger.warning(
                    "scheduler ticker fetch failed ticker=%s error=%s",
                    ticker,
                    exc,
                )
                errors.append(f"{ticker}: {exc}")

        logger.info(
            "scheduler finished external fetch",
            extra={
                "article_count": len(fetched_articles),
                "market_snapshot_count": len(market_snapshots),
                "error_count": len(errors),
            },
        )
        return {
            **state,
            "fetched_articles": fetched_articles,
            "market_snapshots": market_snapshots,
            "errors": errors,
            "metadata": {
                **metadata,
                "fetch_metrics": fetch_metrics,
                "provider_counts": provider_counts,
            },
        }

    async def normalize_dedupe(self, state: SchedulerState) -> SchedulerState:
        logger.info(
            "scheduler normalizing fetched data",
            extra={
                "fetched_article_count": len(state.get("fetched_articles", [])),
                "market_snapshot_count": len(state.get("market_snapshots", [])),
            },
        )
        saved_articles: list[dict[str, Any]] = []
        due_tickers = {ticker.upper() for ticker in state.get("due_tickers", [])}
        accepted_article_count = 0
        rejected_article_count = 0
        duplicate_article_count = 0
        source_quality: dict[str, dict[str, int]] = {}
        classification_metadata: list[dict[str, Any]] = []

        async with self.session_factory() as session:
            article_repo = ArticleRepository(session)
            market_repo = MarketRepository(session)

            for item in state.get("fetched_articles", []):
                title = item["title"]
                text = item.get("summary") or ""
                source_name = item.get("source_name") or "unknown"
                quality = source_quality.setdefault(
                    source_name,
                    {"fetched": 0, "accepted": 0, "rejected": 0, "saved": 0, "duplicates": 0},
                )
                quality["fetched"] += 1
                classification = await self.market_impact_classifier.classify(
                    title=title,
                    text=text,
                    category=item.get("category", ""),
                    source=item.get("source_name", ""),
                    provider=item.get("provider", ""),
                )
                classification_metadata.append(
                    {
                        "title": title[:160],
                        "url": item.get("url"),
                        **classification.metadata(),
                    }
                )
                if not classification.accepted:
                    rejected_article_count += 1
                    quality["rejected"] += 1
                    continue
                accepted_article_count += 1
                quality["accepted"] += 1
                related_tickers = _related_tickers_for_title(title, due_tickers)
                article, created = await article_repo.upsert_article(
                    source_id=item["source_id"],
                    url=item["url"],
                    title=title,
                    published_at=item["published_at"],
                    content_hash=content_hash(title, item.get("summary"), item["url"]),
                    category=item["category"],
                    extracted_text=text,
                    author=item.get("author"),
                    related_tickers=related_tickers,
                )
                if created:
                    quality["saved"] += 1
                    saved_articles.append(
                        {
                            "id": article.id,
                            "title": article.title,
                            "source": item["source_name"],
                            "text": article.extracted_text or article.title,
                        }
                    )
                else:
                    duplicate_article_count += 1
                    quality["duplicates"] += 1

            for snapshot in state.get("market_snapshots", []):
                await market_repo.save_snapshot(
                    symbol=snapshot["symbol"],
                    price=snapshot["price"],
                    percent_change=snapshot["percent_change"],
                    indicators=snapshot["indicators"],
                )

            await session.commit()

        metadata = dict(state.get("metadata", {}))
        fetch_metrics = _ensure_fetch_metrics(metadata)
        source_health = fetch_metrics["sources"].setdefault("health", {})
        for source_name, quality in source_quality.items():
            if quality["fetched"] > 0 and quality["accepted"] == 0:
                source_health[source_name] = "low_signal"
            elif quality["fetched"] > 0 and quality["saved"] == 0 and quality["duplicates"] > 0:
                source_health[source_name] = "low_signal"
        metadata = {
            **state.get("metadata", {}),
            "fetch_metrics": fetch_metrics,
            "saved_article_count": len(saved_articles),
            "accepted_article_count": accepted_article_count,
            "rejected_article_count": rejected_article_count,
            "duplicate_article_count": duplicate_article_count,
            "source_quality": source_quality,
            "market_impact_classifications": classification_metadata[:50],
            "market_snapshot_count": len(state.get("market_snapshots", [])),
        }
        logger.info(
            "scheduler persisted fetched data",
            extra={
                "saved_article_count": len(saved_articles),
                "market_snapshot_count": len(state.get("market_snapshots", [])),
            },
        )
        return {**state, "saved_articles": saved_articles, "metadata": metadata}

    async def embed_store(self, state: SchedulerState) -> SchedulerState:
        pipeline_scope = _pipeline_scope(state.get("job_type", ""), state.get("pipeline_scope"))
        if pipeline_scope == "market_prices":
            logger.info("scheduler skipping embeddings for market price pipeline")
            return {**state, "summaries": state.get("summaries", [])}

        saved_articles = state.get("saved_articles", [])
        if not saved_articles:
            logger.info("scheduler skipping article embeddings; no new articles")
            return state

        logger.info(
            "scheduler storing article embeddings",
            extra={"saved_article_count": len(saved_articles)},
        )
        async with self.session_factory() as session:
            repo = EmbeddingRepository(session)
            for article in saved_articles:
                logger.info(
                    "scheduler embedding article article_id=%s title=%s",
                    article["id"],
                    article["title"][:80],
                )
                embedding = await asyncio.wait_for(
                    self.embedding_service.embed_text(
                        f"{article['title']}\n{article.get('text', '')}"
                    ),
                    timeout=self.settings.llm_timeout_seconds,
                )
                await repo.save_article_embedding(
                    article_id=article["id"],
                    embedding=embedding,
                    embedding_model=self.settings.embedding_model,
                )
            await session.commit()

        logger.info(
            "scheduler stored article embeddings",
            extra={"embedding_count": len(saved_articles)},
        )
        return state

    async def precompute_summaries(self, state: SchedulerState) -> SchedulerState:
        pipeline_scope = _pipeline_scope(state.get("job_type", ""), state.get("pipeline_scope"))
        if pipeline_scope == "market_prices":
            logger.info("scheduler skipping summaries for market price pipeline")
            return {**state, "summaries": []}

        summaries: list[str] = []
        async with self.session_factory() as session:
            articles = await ArticleRepository(session).list_without_summaries(limit=20)
            logger.info(
                "scheduler precomputing summaries",
                extra={"article_count": len(articles)},
            )
            summary_repo = SummaryRepository(session)
            for article in articles:
                text = article.extracted_text or article.title
                logger.info(
                    "scheduler summarizing article article_id=%s title=%s timeout=%ss",
                    article.id,
                    article.title[:80],
                    self.settings.llm_timeout_seconds,
                )
                summary_text = await asyncio.wait_for(
                    self.summarizer.summarize_article(
                        SummaryRequest(
                            title=article.title,
                            text=text,
                            source=str(article.source_id or "unknown"),
                        )
                    ),
                    timeout=self.settings.llm_timeout_seconds,
                )
                summary = await summary_repo.save_article_summary(
                    article_id=article.id,
                    text=summary_text,
                    model_name=self.settings.openai_model,
                )
                logger.info("scheduler embedding summary summary_id=%s", summary.id)
                embedding = await asyncio.wait_for(
                    self.embedding_service.embed_text(summary_text),
                    timeout=self.settings.llm_timeout_seconds,
                )
                await EmbeddingRepository(session).save_summary_embedding(
                    summary.id,
                    embedding,
                    self.settings.embedding_model,
                )
                summaries.append(summary_text)
            await session.commit()
        logger.info("scheduler stored summaries", extra={"summary_count": len(summaries)})
        return {**state, "summaries": summaries}

    async def quality_check(self, state: SchedulerState) -> SchedulerState:
        logger.info(
            "scheduler quality check",
            extra={
                "summary_count": len(state.get("summaries", [])),
                "error_count": len(state.get("errors", [])),
            },
        )
        return state

    async def extract_mentions(self, state: SchedulerState) -> SchedulerState:
        pipeline_scope = _pipeline_scope(state.get("job_type", ""), state.get("pipeline_scope"))
        if pipeline_scope == "market_prices":
            return state

        async with self.session_factory() as session:
            count = await extract_market_mentions(session, self.settings, limit=100)
            await session.commit()
        metadata = {**state.get("metadata", {}), "mention_count": count}
        return {**state, "metadata": metadata}

    async def sector_enrichment(self, state: SchedulerState) -> SchedulerState:
        pipeline_scope = _pipeline_scope(state.get("job_type", ""), state.get("pipeline_scope"))
        if pipeline_scope == "market_prices":
            return state

        async with self.session_factory() as session:
            count = await enrich_market_sectors(session, self.settings)
        metadata = {**state.get("metadata", {}), "sector_context_count": count}
        return {**state, "metadata": metadata}

    async def evidence_backfill(self, state: SchedulerState) -> SchedulerState:
        pipeline_scope = _pipeline_scope(state.get("job_type", ""), state.get("pipeline_scope"))
        if pipeline_scope == "market_prices":
            return state

        async with self.session_factory() as session:
            count = await backfill_signal_evidence_links(session)
            await session.commit()
        metadata = {**state.get("metadata", {}), "signal_evidence_backfill_count": count}
        return {**state, "metadata": metadata}

    async def score_signals(self, state: SchedulerState) -> SchedulerState:
        pipeline_scope = _pipeline_scope(state.get("job_type", ""), state.get("pipeline_scope"))
        if pipeline_scope == "market_prices":
            return state

        async with self.session_factory() as session:
            count = await score_market_signals(session, self.settings)
            await session.commit()
        metadata = {**state.get("metadata", {}), "signal_count": count}
        return {**state, "metadata": metadata}

    async def confidence_filter(self, state: SchedulerState) -> SchedulerState:
        pipeline_scope = _pipeline_scope(state.get("job_type", ""), state.get("pipeline_scope"))
        if pipeline_scope == "market_prices":
            return state

        async with self.session_factory() as session:
            count = await count_confident_signal_context(session, self.settings)
        metadata = {**state.get("metadata", {}), "confident_signal_count": count}
        return {**state, "metadata": metadata}

    async def cleanup_market_research(self, state: SchedulerState) -> SchedulerState:
        async with self.session_factory() as session:
            count = await prune_market_research_data(session, self.settings)
            await session.commit()
        metadata = {**state.get("metadata", {}), "market_research_pruned_count": count}
        return {**state, "metadata": metadata}

    async def retry_or_recover(self, state: SchedulerState) -> SchedulerState:
        job_id = state.get("job_id")
        if not job_id:
            logger.warning("scheduler missing job_id during completion")
            return state

        async with self.session_factory() as session:
            job_repo = JobRepository(session)
            job = await session.get(JobRun, job_id)
            if job:
                errors = state.get("errors", [])
                await job_repo.finish(
                    job,
                    status="completed_with_errors" if errors else "completed",
                    error_message="\n".join(errors) if errors else None,
                )
                await session.commit()
                logger.info(
                    "scheduler job finished",
                    extra={
                        "job_id": job_id,
                        "status": "completed_with_errors" if errors else "completed",
                        "error_count": len(errors),
                    },
                )
        return state


def _parse_symbol_csv(value: str) -> list[str]:
    return [
        item.strip().upper()
        for item in value.split(",")
        if item.strip() and re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,14}", item.strip())
    ]


def _source_is_due(source: Source, settings: Settings, now: datetime | None = None) -> bool:
    if source.last_fetched_at is None:
        return True
    now = now or datetime.now(UTC)
    last_fetched_at = source.last_fetched_at
    if last_fetched_at.tzinfo is None:
        last_fetched_at = last_fetched_at.replace(tzinfo=UTC)
    interval = _config_int(
        dict(source.config or {}),
        "fetch_interval_seconds",
        settings.source_default_fetch_interval_seconds,
    )
    return (now - last_fetched_at).total_seconds() >= max(interval, 0)


def _pipeline_scope(job_type: str, explicit_scope: object = None) -> str:
    if isinstance(explicit_scope, str) and explicit_scope.strip():
        return explicit_scope.strip()
    if job_type in {"market_prices", "breaking_resources", "daily_resources"}:
        return job_type
    return "all"


def _source_in_pipeline(source: Source, pipeline_scope: str) -> bool:
    config = dict(source.config or {})
    raw_tier = config.get("pipeline_tier") or config.get("pipeline_tiers")
    if raw_tier is None:
        return pipeline_scope == "breaking_resources"
    if isinstance(raw_tier, list):
        tiers = {str(item).strip().lower() for item in raw_tier if str(item).strip()}
    else:
        tiers = {item.strip().lower() for item in str(raw_tier).split(",") if item.strip()}
    aliases = {
        "breaking_resources": {"breaking", "breaking_resources", "important"},
        "daily_resources": {"daily", "daily_resources", "general"},
    }
    return bool(tiers & aliases.get(pipeline_scope, {pipeline_scope}))


def _limit_articles_for_source(source: dict[str, Any], articles: list, settings: Settings) -> list:
    config = dict(source.get("config") or {})
    max_items = _config_int(config, "max_items", settings.source_max_items_per_fetch)
    max_age_hours = _config_int(config, "max_item_age_hours", settings.source_max_item_age_hours)
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    filtered = [
        article
        for article in articles
        if article.published_at is None or _aware_datetime(article.published_at) >= cutoff
    ]
    return filtered[: max(max_items, 0)]


def _exclude_items_for_source(source: dict[str, Any], articles: list, settings: Settings) -> list:
    config = dict(source.get("config") or {})
    excluded_names = _excluded_source_names(config, settings)
    if not excluded_names:
        return articles
    return [
        article
        for article in articles
        if not _article_matches_excluded_name(article, excluded_names)
    ]


def _excluded_source_names(config: dict[str, Any], settings: Settings) -> set[str]:
    values: list[str] = []
    raw_config = config.get("excluded_sources") or config.get("exclude_sources")
    if isinstance(raw_config, list):
        values.extend(str(item) for item in raw_config)
    elif isinstance(raw_config, str):
        values.extend(raw_config.split(","))
    values.extend(settings.source_excluded_names.split(","))
    return {value.strip().lower() for value in values if value.strip()}


def _article_matches_excluded_name(article, excluded_names: set[str]) -> bool:
    haystack = [
        getattr(article, "author", None),
        getattr(article, "account", None),
        getattr(article, "provider", None),
        getattr(article, "title", None),
    ]
    metadata = getattr(article, "metadata", {}) or {}
    if isinstance(metadata, dict):
        haystack.extend(str(value) for value in metadata.values() if value is not None)
    text = " ".join(str(value) for value in haystack if value).lower()
    return any(name in text for name in excluded_names)


def _config_int(config: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _default_sources_from_settings(settings: Settings) -> list[dict[str, object]]:
    raw = settings.default_sources_json.strip()
    if not raw:
        if not settings.default_source_pack_enabled:
            return []
        try:
            raw = DEFAULT_SOURCE_PACK_PATH.read_text()
        except OSError:
            logger.warning("default source pack could not be read")
            return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("invalid DEFAULT_SOURCES_JSON; no default sources will be created")
        return []
    if not isinstance(parsed, list):
        logger.warning("DEFAULT_SOURCES_JSON must be a JSON array")
        return []
    return [
        item
        for item in parsed
        if isinstance(item, dict) and _source_has_required_credentials(item, settings)
    ]


def _source_has_required_credentials(item: dict[str, object], settings: Settings) -> bool:
    provider = str(item.get("provider") or "").strip().lower()
    config = dict(item.get("config") or {})
    if provider == "alpha_vantage":
        return bool(config.get("api_key") or settings.alpha_vantage_api_key)
    if provider == "finnhub":
        return bool(config.get("api_key") or settings.finnhub_api_key)
    if provider == "polygon":
        return bool(config.get("api_key") or settings.polygon_api_key)
    return True


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _related_tickers_for_title(title: str, due_tickers: set[str]) -> list[str]:
    matches: list[str] = []
    for ticker in sorted(due_tickers):
        if len(ticker) == 1:
            if re.search(rf"\${re.escape(ticker)}(?:\b|$)", title, flags=re.IGNORECASE):
                matches.append(ticker)
            continue
        if re.search(rf"(?<![A-Za-z0-9$]){re.escape(ticker)}(?![A-Za-z0-9])", title):
            matches.append(ticker)
    return matches


def _is_refresh_workflow(workflow: str) -> bool:
    return workflow in {
        "market_research_refresh",
        "manual_refresh",
        "news_refresh",
        "scheduler",
        "market_prices",
        "breaking_resources",
        "daily_resources",
    }


def _ensure_fetch_metrics(metadata: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(metadata.get("fetch_metrics") or {})
    sources = dict(metrics.get("sources") or {})
    tickers = dict(metrics.get("tickers") or {})
    metrics["sources"] = sources
    metrics["tickers"] = tickers
    metrics["retry_count"] = int(metrics.get("retry_count", 0) or 0)
    metrics["failures"] = list(metrics.get("failures") or [])
    return metrics
