import asyncio
import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.agent.chains import build_brief_response, build_stocks_response
from news_agent.agent.guardrails import enforce_financial_guardrails
from news_agent.agent.intent import IntentClassifier
from news_agent.agent.ranking import rank_articles
from news_agent.agent.react import ReActResponder
from news_agent.agent.router import route_intent
from news_agent.agent.tools import ToolRegistry
from news_agent.graph.state import NewsAgentState, SchedulerState
from news_agent.ingestion.dedupe import content_hash
from news_agent.ingestion.providers import IngestProviderRegistry
from news_agent.markets.yahoo import YahooMarketDataProvider
from news_agent.memory.embeddings import EmbeddingService
from news_agent.memory.long_term import memory_from_user_text, should_store_memory
from news_agent.memory.short_term import append_message, expiry
from news_agent.observability.runtime import RuntimeAlertService, RuntimeTraceService, summarize_run_state
from news_agent.settings import Settings
from news_agent.storage.models import JobRun, Source
from news_agent.storage.repositories import (
    ArticleRepository,
    EmbeddingRepository,
    JobRepository,
    MarketRepository,
    MemoryRepository,
    PreferenceRepository,
    ShortTermSessionRepository,
    SourceRepository,
    SummaryRepository,
    TickerRepository,
    UserRepository,
)
from news_agent.storage.retrieval import RetrievalService
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


class GraphNodes:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.embedding_service = EmbeddingService(settings)
        self.react_responder = ReActResponder(settings)
        self.intent_classifier = IntentClassifier(settings)
        self.market_provider = YahooMarketDataProvider()
        self.tool_registry = ToolRegistry(
            session_factory,
            settings,
            self.market_provider,
            self.react_responder,
        )

    async def parse_intent(self, state: NewsAgentState) -> NewsAgentState:
        message_text = state.get("message_text", "")
        command, args, intent = await self.intent_classifier.classify(message_text)
        logger.info(
            "chat routed message intent=%s command=%s text=%s",
            intent,
            command or "-",
            message_text[:200],
        )
        return {**state, "command": command, "args": args, "intent": intent}

    async def load_user_state(self, state: NewsAgentState) -> NewsAgentState:
        async with self.session_factory() as session:
            user = await UserRepository(session, self.settings).get_or_create_user(
                state["telegram_user_id"]
            )
            preference = await PreferenceRepository(session).get_for_user(user.id)
            tickers = await TickerRepository(session).list_for_user(user.id)
            memories = await MemoryRepository(session).list_for_user(user.id)
            short_term_state = await ShortTermSessionRepository(session).get_state(state["chat_id"])
            await session.commit()

        logger.info(
            "chat loaded user state user_id=%s topics=%s tickers=%s short_term_messages=%s",
            user.id,
            len(preference.topics),
            len(tickers),
            len(short_term_state.get("messages", [])),
        )
        return {
            **state,
            "user_id": user.id,
            "local_region": user.local_region,
            "topics": preference.topics,
            "watched_tickers": tickers,
            "short_term_memory": short_term_state,
            "long_term_memory": [memory.memory_text for memory in memories],
        }

    async def route_request(self, state: NewsAgentState) -> NewsAgentState:
        route = route_intent(state.get("intent", "unknown"))
        logger.info(
            "chat routed to subagent=%s tool=%s needs_context=%s",
            route.subagent,
            route.tool_name,
            route.needs_context,
        )
        return {
            **state,
            "subagent": route.subagent,
            "tool_name": route.tool_name,
            "needs_context": route.needs_context,
        }

    async def apply_tool_or_skill(self, state: NewsAgentState) -> NewsAgentState:
        result = await self.tool_registry.run(state["tool_name"], state)
        next_state = {
            **state,
            **result.updates,
            "tool_result": {
                "subagent": state.get("subagent"),
                "tool_name": state.get("tool_name"),
                "has_response": result.response is not None,
            },
        }
        if result.response is not None:
            next_state["response"] = result.response
        return next_state

    async def apply_command(self, state: NewsAgentState) -> NewsAgentState:
        intent = state.get("intent", "unknown")
        args = state.get("args", [])
        user_id = state["user_id"]

        async with self.session_factory() as session:
            if intent == "watch":
                added = await TickerRepository(session).add_many(user_id, args)
                await session.commit()
                response = f"Now watching: {', '.join(added) or 'no new tickers'}"
                return {**state, "response": response}

            if intent == "unwatch":
                removed = await TickerRepository(session).remove_many(user_id, args)
                await session.commit()
                return {**state, "response": f"Removed: {', '.join(removed) or 'none'}"}

            if intent == "topics":
                preference = await PreferenceRepository(session).set_topics(user_id, args)
                await session.commit()
                response = f"Topics updated: {', '.join(preference.topics)}"
                return {**state, "topics": preference.topics, "response": response}

            if intent == "local" and args:
                local_region = " ".join(args)
                user = await UserRepository(session, self.settings).set_local_region(
                    user_id, local_region
                )
                await session.commit()
                return {
                    **state,
                    "local_region": user.local_region if user else local_region,
                    "response": f"Local region updated: {local_region}",
                }

            if intent == "addsource" and args:
                url = args[0]
                source = await SourceRepository(session).add_source(
                    name=url.split("//")[-1].split("/")[0],
                    url=url,
                    owner_user_id=user_id,
                )
                await session.commit()
                return {**state, "response": f"Added source {source.name}: {source.url}"}

            if intent == "removesource" and args:
                try:
                    source_id = int(args[0])
                except ValueError:
                    return {**state, "response": "Usage: /removesource <source-id>"}

                removed = await SourceRepository(session).disable_source(source_id, user_id)
                await session.commit()
                response = "Source removed." if removed else "Source not found or not removable."
                return {**state, "response": response}

            if intent == "memory":
                memories = await MemoryRepository(session).list_for_user(user_id)
                short_term_state = await ShortTermSessionRepository(session).get_state(
                    state["chat_id"]
                )
                response_parts: list[str] = []
                messages = short_term_state.get("messages", [])[-8:]
                if messages:
                    response_parts.append(
                        "Recent session memory:\n"
                        + "\n".join(
                            f"- {item.get('role')}: {item.get('content')}" for item in messages
                        )
                    )
                if memories:
                    response_parts.append(
                        "Long-term memory:\n"
                        + "\n".join(
                            f"- {memory.public_id}: {memory.memory_text}" for memory in memories
                        )
                    )
                response = "\n\n".join(response_parts) or "No memory saved yet."
                return {**state, "response": response}

            if intent == "forget" and args:
                removed = await MemoryRepository(session).forget(user_id, args[0])
                await session.commit()
                response = "Memory removed." if removed else "Memory not found."
                return {**state, "response": response}

            if intent == "resetmemory":
                await MemoryRepository(session).reset_learned(user_id)
                await session.commit()
                return {**state, "response": "Learned memory has been reset."}

        return state

    async def persist_memory(self, state: NewsAgentState) -> NewsAgentState:
        text = state.get("message_text", "")
        short_term_state = dict(state.get("short_term_memory", {}))
        append_message(short_term_state, "user", text)
        if state.get("response"):
            append_message(short_term_state, "assistant", state["response"])

        async with self.session_factory() as session:
            await ShortTermSessionRepository(session).save_state(
                state["chat_id"],
                short_term_state,
                expiry(),
            )
            await session.commit()

        if not should_store_memory(text):
            logger.info(
                "chat persisted short-term memory chat_id=%s message_count=%s",
                state["chat_id"],
                len(short_term_state.get("messages", [])),
            )
            return {**state, "short_term_memory": short_term_state}

        async with self.session_factory() as session:
            memory = await MemoryRepository(session).remember(
                user_id=state["user_id"],
                text=memory_from_user_text(text),
            )
            embedding = await self.embedding_service.embed_text(memory.memory_text)
            await EmbeddingRepository(session).save_memory_embedding(
                memory.id,
                embedding,
                self.settings.embedding_model,
            )
            await session.commit()

        logger.info(
            "chat persisted long-term memory user_id=%s chat_id=%s",
            state["user_id"],
            state["chat_id"],
        )
        return {**state, "short_term_memory": short_term_state}

    async def retrieve_context(self, state: NewsAgentState) -> NewsAgentState:
        async with self.session_factory() as session:
            context = await RetrievalService(session).retrieve_for_brief(
                user_id=state["user_id"],
                topics=state.get("topics", []),
                tickers=state.get("watched_tickers", []),
            )

        articles: list[dict[str, Any]] = [
            {
                "id": article.id,
                "title": article.title,
                "source": article.source_id,
                "published_at": article.published_at,
                "related_tickers": article.related_tickers,
            }
            for article in context.articles
        ]

        return {
            **state,
            "retrieved_articles": articles,
            "retrieved_summaries": [summary.text for summary in context.summaries],
            "market_context": [
                {
                    "symbol": snapshot.symbol,
                    "price": snapshot.price,
                    "percent_change": snapshot.percent_change,
                    "indicators": snapshot.indicators,
                }
                for snapshot in context.market_snapshots
            ],
        }

    async def rank_context(self, state: NewsAgentState) -> NewsAgentState:
        ranked = rank_articles(
            state.get("retrieved_articles", []),
            state.get("topics", []),
            state.get("watched_tickers", []),
            state.get("local_region"),
        )
        return {**state, "retrieved_articles": ranked}

    async def compose_response(self, state: NewsAgentState) -> NewsAgentState:
        if state.get("response"):
            return state

        tool_name = state.get("tool_name")
        if tool_name == "news_brief":
            response = build_brief_response(
                state.get("retrieved_articles", []),
                state.get("retrieved_summaries", []),
                state.get("market_context", []),
                state.get("local_region", self.settings.default_local_region),
            )
            return {**state, "response": response}

        if tool_name == "general_chat":
            result = await self.react_responder.respond(
                state.get("message_text", ""),
                {
                    "articles": state.get("retrieved_articles", []),
                    "summaries": state.get("retrieved_summaries", []),
                    "market_context": state.get("market_context", []),
                    "memories": state.get("long_term_memory", []),
                    "recent_messages": state.get("short_term_memory", {}).get("messages", []),
                    "tickers": state.get("watched_tickers", []),
                    "local_region": state.get("local_region"),
                },
            )
            metadata = {
                **state.get("metadata", {}),
                "react_action": result.action,
                "react_observation": result.observation,
            }
            return {**state, "response": result.answer, "metadata": metadata}

        return {
            **state,
            "response": "I can help with news briefs, stock context, sources, topics, and memory.",
        }

    async def generate_response(self, state: NewsAgentState) -> NewsAgentState:
        intent = state.get("intent", "unknown")
        if state.get("response"):
            return state

        if intent == "stocks":
            response_path = "stocks"
            response = build_stocks_response(
                state.get("watched_tickers", []), state.get("market_context", [])
            )
        elif intent == "brief":
            response_path = "brief"
            response = build_brief_response(
                state.get("retrieved_articles", []),
                state.get("retrieved_summaries", []),
                state.get("market_context", []),
                state.get("local_region", self.settings.default_local_region),
            )
        elif intent in {"general_chat", "unknown"}:
            response_path = "react"
            result = await self.react_responder.respond(
                state.get("message_text", ""),
                {
                    "articles": state.get("retrieved_articles", []),
                    "summaries": state.get("retrieved_summaries", []),
                    "market_context": state.get("market_context", []),
                    "memories": state.get("long_term_memory", []),
                    "recent_messages": state.get("short_term_memory", {}).get("messages", []),
                    "tickers": state.get("watched_tickers", []),
                    "local_region": state.get("local_region"),
                },
            )
            response = result.answer
            metadata = {
                **state.get("metadata", {}),
                "react_action": result.action,
                "react_observation": result.observation,
            }
            state = {**state, "metadata": metadata}
            logger.info(
                "chat react response action=%s observation=%s",
                result.action,
                result.observation,
            )
        elif intent == "sources":
            response_path = "sources"
            async with self.session_factory() as session:
                sources = await SourceRepository(session).list_enabled(state["user_id"])
            if sources:
                response = "Enabled sources:\n" + "\n".join(
                    f"- {source.id}: {source.name} ({source.category})" for source in sources
                )
            else:
                response = "No sources enabled yet. Use /addsource <rss-url> to add one."
        elif intent == "help":
            response_path = "help"
            response = (
                "Commands: /brief, /stocks, /watch, /unwatch, /topics, /local, "
                "/addsource, /removesource, /sources, /memory, /resetmemory."
            )
        else:
            response_path = "fallback"
            response = "I can help with news briefs, stock context, sources, topics, and memory."

        logger.info(
            "chat generated response path=%s intent=%s response_chars=%s",
            response_path,
            intent,
            len(response),
        )
        return {**state, "response": response}

    async def guardrail_check(self, state: NewsAgentState) -> NewsAgentState:
        return {**state, "response": enforce_financial_guardrails(state.get("response", ""))}


class SchedulerNodes:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.market_provider = YahooMarketDataProvider()
        self.summarizer = Summarizer(settings)
        self.embedding_service = EmbeddingService(settings)
        self.ingest_registry = IngestProviderRegistry()
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
                await self.trace_service.finish_step(step_id, status="failed", error_message=message)
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
        job_type = state.get("job_type", "news_refresh")
        logger.info("scheduler loading due work", extra={"job_type": job_type})
        async with self.session_factory() as session:
            source_repo = SourceRepository(session)
            sources = await source_repo.list_all_enabled()
            if not sources:
                logger.info("scheduler creating default sources")
                sources = await source_repo.ensure_default_sources()
            tickers = await TickerRepository(session).list_all_symbols()
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
                provider_counts[source["provider"]] = provider_counts.get(source["provider"], 0) + len(articles)
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

        async with self.session_factory() as session:
            article_repo = ArticleRepository(session)
            market_repo = MarketRepository(session)

            for item in state.get("fetched_articles", []):
                title = item["title"]
                related_tickers = [ticker for ticker in due_tickers if ticker in title.upper()]
                article, created = await article_repo.upsert_article(
                    source_id=item["source_id"],
                    url=item["url"],
                    title=title,
                    published_at=item["published_at"],
                    content_hash=content_hash(title, item.get("summary"), item["url"]),
                    category=item["category"],
                    extracted_text=item.get("summary"),
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
