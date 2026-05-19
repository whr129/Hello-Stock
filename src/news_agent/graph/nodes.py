import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
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
    RuntimeAlertService,
    RuntimeTraceService,
    summarize_run_state,
)
from news_agent.research.scheduler import (
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
        self.ingest_registry = IngestProviderRegistry()
        self.market_impact_classifier = MarketImpactClassifier(settings)
        self.trace_service = RuntimeTraceService(session_factory, settings)
        self.alert_service = RuntimeAlertService(session_factory, settings)

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

    async def load_due_sources(self, state: SchedulerState) -> SchedulerState:
        job_type = state.get("job_type", "market_research_refresh")
        logger.info("scheduler loading due work", extra={"job_type": job_type})
        async with self.session_factory() as session:
            source_repo = SourceRepository(session)
            sources = await source_repo.list_all_enabled()
            if not sources:
                default_sources = _default_sources_from_settings(self.settings)
                if default_sources:
                    logger.info(
                        "scheduler creating configured default sources",
                        extra={"source_count": len(default_sources)},
                    )
                    sources = await source_repo.ensure_default_sources(default_sources)
            sources = [source for source in sources if _source_is_due(source, self.settings)]
            tickers = await self._market_universe_symbols(session)
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
        logger.info(
            "scheduler fetching feeds",
            extra={"source_count": len(state.get("due_sources", []))},
        )

        for source in state.get("due_sources", []):
            provider_step_id: int | None = None
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
                articles = await self._run_blocking_with_timeout(
                    label=f"source source={source['name']}",
                    func=lambda payload=source_payload, provider=provider: provider.fetch_items(
                        _source_dict_to_model(payload),
                        timeout_seconds=self.settings.rss_fetch_timeout_seconds,
                    ),
                    timeout_seconds=self.settings.rss_fetch_timeout_seconds + 2,
                )
                articles = _limit_articles_for_source(source, articles, self.settings)
                provider_counts[source["provider"]] = provider_counts.get(
                    source["provider"],
                    0,
                ) + len(articles)
                await self.trace_service.finish_step(
                    provider_step_id,
                    status="completed",
                    metadata={"article_count": len(articles)},
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
                if provider_step_id is not None:
                    await self.trace_service.finish_step(
                        provider_step_id,
                        status="failed",
                        error_message=str(exc),
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
                snapshot = await self._run_blocking_with_timeout(
                    label=f"ticker ticker={ticker}",
                    func=lambda symbol=ticker_symbol: self.market_provider.get_snapshot(symbol),
                    timeout_seconds=self.settings.market_fetch_timeout_seconds,
                )
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
                    metadata={"symbol": snapshot.symbol},
                )
                logger.info(
                    "scheduler fetched ticker ticker=%s price=%s percent_change=%s",
                    snapshot.symbol,
                    snapshot.price,
                    snapshot.percent_change,
                )
            except Exception as exc:
                if provider_step_id is not None:
                    await self.trace_service.finish_step(
                        provider_step_id,
                        status="failed",
                        error_message=str(exc),
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
            "metadata": {**state.get("metadata", {}), "provider_counts": provider_counts},
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
        rejected_article_count = 0
        classification_metadata: list[dict[str, Any]] = []

        async with self.session_factory() as session:
            article_repo = ArticleRepository(session)
            market_repo = MarketRepository(session)

            for item in state.get("fetched_articles", []):
                title = item["title"]
                text = item.get("summary") or ""
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
                    continue
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
                    saved_articles.append(
                        {
                            "id": article.id,
                            "title": article.title,
                            "source": item["source_name"],
                            "text": article.extracted_text or article.title,
                        }
                    )

            for snapshot in state.get("market_snapshots", []):
                await market_repo.save_snapshot(
                    symbol=snapshot["symbol"],
                    price=snapshot["price"],
                    percent_change=snapshot["percent_change"],
                    indicators=snapshot["indicators"],
                )

            await session.commit()

        metadata = {
            **state.get("metadata", {}),
            "saved_article_count": len(saved_articles),
            "rejected_article_count": rejected_article_count,
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
        async with self.session_factory() as session:
            count = await extract_market_mentions(session, self.settings, limit=100)
            await session.commit()
        metadata = {**state.get("metadata", {}), "mention_count": count}
        return {**state, "metadata": metadata}

    async def score_signals(self, state: SchedulerState) -> SchedulerState:
        async with self.session_factory() as session:
            count = await score_market_signals(session, self.settings)
            await session.commit()
        metadata = {**state.get("metadata", {}), "signal_count": count}
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
        if item.strip() and item.strip().replace(".", "").isalpha()
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


def _config_int(config: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _default_sources_from_settings(settings: Settings) -> list[dict[str, object]]:
    raw = settings.default_sources_json.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("invalid DEFAULT_SOURCES_JSON; no default sources will be created")
        return []
    if not isinstance(parsed, list):
        logger.warning("DEFAULT_SOURCES_JSON must be a JSON array")
        return []
    return [item for item in parsed if isinstance(item, dict)]


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
