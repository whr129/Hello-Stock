import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.agent.router import skills_response
from news_agent.app.state import AgentResult, SupervisorState
from news_agent.ingestion.providers import IngestProviderRegistry
from news_agent.memory.consolidation import MemoryConsolidationService
from news_agent.memory.short_term import render_messages
from news_agent.scheduler.service import (
    SchedulerControlService,
    parse_config_value,
)
from news_agent.settings import Settings
from news_agent.storage.models import (
    Article,
    ArticleEmbedding,
    ConversationEvent,
    JobRun,
    LongTermMemory,
    MarketEntity,
    MarketMention,
    MarketSignalSnapshot,
    MarketSnapshot,
    MarketThemeMemory,
    MemoryConsolidationJob,
    RuntimeRun,
    Source,
    Summary,
    SummaryEmbedding,
)
from news_agent.storage.repositories import (
    MemoryRepository,
    SourceRepository,
)


@dataclass(frozen=True)
class ResourceSection:
    name: str
    count: int
    details: tuple[str, ...] = ()


SOURCE_PACK_PATH = Path(__file__).resolve().parents[4] / "docs/market-research/default-sources.json"


class NewsSubagent:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.ingest_registry = IngestProviderRegistry(settings)
        self.scheduler_control = SchedulerControlService(settings)
        self.memory_service = MemoryConsolidationService(session_factory, settings)

    async def run(self, state: SupervisorState) -> AgentResult:
        capabilities = set(state.get("route", {}).get("capabilities", []))
        if "help" in capabilities:
            return {
                "response": (
                    "I can route requests between market research, "
                    "runtime inspection, and general web search. "
                    "Try /skills for the full command list, or ask a general question directly."
                ),
                "metadata": {"capability": "help"},
            }
        if "skills" in capabilities:
            return {
                "response": skills_response(),
                "metadata": {"capability": "skills"},
            }
        if "resource_inventory" in capabilities:
            return await self._resource_inventory(state)
        if "scheduler_admin" in capabilities:
            return await self._scheduler_admin(state)
        if "source_admin" in capabilities:
            return await self._source_admin(state)
        if "memory_admin" in capabilities:
            return await self._memory_admin(state)
        return {
            "response": (
                "This assistant focuses on market-impact research, source management, "
                "runtime inspection, and memory. Use /research, /candidates, /signals, "
                "/sources, or /skills."
            ),
            "metadata": {"capability": "help"},
        }

    async def _scheduler_admin(self, state: SupervisorState) -> AgentResult:
        pipeline = _parse_refresh_pipeline(state.get("args", []))
        if pipeline is None:
            return {
                "response": _refresh_usage(),
                "metadata": {"capability": "scheduler_admin", "status": "invalid_pipeline"},
            }
        if not await self.scheduler_control.can_start_refresh():
            return {
                "response": "A refresh job is already running. Try again in a moment.",
                "metadata": {"capability": "scheduler_admin"},
            }
        summary = await self.scheduler_control.run_refresh(job_type=pipeline)
        return {
            "response": self.scheduler_control.format_refresh_summary(summary),
            "metadata": {"capability": "scheduler_admin", "pipeline": pipeline},
        }

    async def _source_admin(self, state: SupervisorState) -> AgentResult:
        command = state.get("command", "")
        args = state.get("args", [])
        user_id = state["user_context"]["user_id"]

        async with self.session_factory() as session:
            repository = SourceRepository(session)
            if command == "/sources":
                sources = await repository.list_enabled(user_id)
                if not sources:
                    response = (
                        "No sources enabled yet. Use /addsource <provider> <account-or-target>."
                    )
                else:
                    response = "Enabled sources:\n" + "\n".join(
                        f"- {source.id}: {source.name} [{source.provider}] "
                        f"{source.external_account} ({source.category})"
                        for source in sources
                    )
                return {"response": response, "metadata": {"capability": "source_admin"}}

            if command == "/addsource":
                if len(args) < 2:
                    return {
                        "response": (
                            "Usage: /addsource <provider> <account-or-target>. "
                            "Examples: /addsource rss https://example.com/feed.xml, "
                            "/addsource twitter @openai, "
                            "/addsource newsletter example-newsletter"
                        ),
                        "metadata": {"capability": "source_admin"},
                    }
                provider = args[0].lower()
                external_account = args[1]
                if provider not in {
                    "rss",
                    "twitter",
                    "newsletter",
                    "alpha_vantage",
                    "finnhub",
                    "polygon",
                }:
                    return {
                        "response": (
                            "Supported source providers: rss, twitter, newsletter, "
                            "alpha_vantage, finnhub, polygon."
                        ),
                        "metadata": {"capability": "source_admin"},
                    }
                config = {"feed_url": external_account} if provider == "rss" else {}
                fetch_mode = "rss" if provider in {"rss", "twitter", "newsletter"} else None
                source = await repository.add_source(
                    name=external_account,
                    provider=provider,
                    external_account=external_account,
                    owner_user_id=user_id,
                    config=config,
                    fetch_mode=fetch_mode,
                )
                await session.commit()
                warning = _source_config_warning(source.provider, source.config)
                return {
                    "response": (
                        f"Added source {source.name} [{source.provider}] "
                        f"{source.external_account}. "
                        f"{warning}"
                    ),
                    "metadata": {"capability": "source_admin"},
                }

            if command == "/sourceconfig":
                if len(args) < 3:
                    return {
                        "response": "Usage: /sourceconfig <source-id> <key> <value>",
                        "metadata": {"capability": "source_admin"},
                    }
                source_id = _parse_source_id(args[0])
                if source_id is None:
                    return {
                        "response": "Usage: /sourceconfig <source-id> <key> <value>",
                        "metadata": {"capability": "source_admin"},
                    }
                key = args[1]
                value = parse_config_value(" ".join(args[2:]))
                source = await repository.update_config_field(source_id, key, value)
                if source is None:
                    return {
                        "response": "Source not found.",
                        "metadata": {"capability": "source_admin"},
                    }
                await session.commit()
                warning = _source_config_warning(source.provider, source.config)
                return {
                    "response": f"Updated source {source.id} config {key}={value}. {warning}",
                    "metadata": {"capability": "source_admin"},
                }

            if command == "/sourcefields":
                if len(args) < 3:
                    return {
                        "response": "Usage: /sourcefields <source-id> <field> <mapped-value>",
                        "metadata": {"capability": "source_admin"},
                    }
                source_id = _parse_source_id(args[0])
                if source_id is None:
                    return {
                        "response": "Usage: /sourcefields <source-id> <field> <mapped-value>",
                        "metadata": {"capability": "source_admin"},
                    }
                source = await repository.update_field_mapping(
                    source_id,
                    args[1],
                    " ".join(args[2:]),
                )
                if source is None:
                    return {
                        "response": "Source not found.",
                        "metadata": {"capability": "source_admin"},
                    }
                await session.commit()
                return {
                    "response": f"Updated source {source.id} field mapping {args[1]}.",
                    "metadata": {"capability": "source_admin"},
                }

            if command == "/sourcetest":
                if not args:
                    return {
                        "response": "Usage: /sourcetest <source-id>",
                        "metadata": {"capability": "source_admin"},
                    }
                source_id = _parse_source_id(args[0])
                if source_id is None:
                    return {
                        "response": "Usage: /sourcetest <source-id>",
                        "metadata": {"capability": "source_admin"},
                    }
                source = await repository.get_by_id(source_id)
                if source is None:
                    return {
                        "response": "Source not found.",
                        "metadata": {"capability": "source_admin"},
                    }
                try:
                    provider = self.ingest_registry.get(source.provider)
                    items = provider.fetch_items(
                        source,
                        timeout_seconds=self.settings.rss_fetch_timeout_seconds,
                    )
                except ValueError as exc:
                    return {
                        "response": f"Source test failed: {exc}",
                        "metadata": {"capability": "source_admin"},
                    }
                preview = "\n".join(f"- {item.title}" for item in items[:3]) or "- no items"
                return {
                    "response": (
                        f"Source test completed for {source.name}.\n"
                        f"- Items fetched: {len(items)}\n"
                        f"- Preview:\n{preview}"
                    ),
                    "metadata": {"capability": "source_admin"},
                }

            if command == "/sourcepack":
                return {
                    "response": _format_source_pack(args),
                    "metadata": {"capability": "source_admin"},
                }

            if command == "/removesource":
                if not args:
                    return {
                        "response": "Usage: /removesource <source-id>",
                        "metadata": {"capability": "source_admin"},
                    }
                source_id = _parse_source_id(args[0])
                if source_id is None:
                    return {
                        "response": "Usage: /removesource <source-id>",
                        "metadata": {"capability": "source_admin"},
                    }

                removed = await repository.disable_source(source_id, user_id)
                await session.commit()
                return {
                    "response": (
                        "Source removed." if removed else "Source not found or not removable."
                    ),
                    "metadata": {"capability": "source_admin"},
                }

        return {
            "response": "Source management request could not be completed.",
            "metadata": {"capability": "source_admin"},
        }

    async def _memory_admin(self, state: SupervisorState) -> AgentResult:
        command = state.get("command", "")
        args = state.get("args", [])
        user_id = state["user_context"]["user_id"]

        async with self.session_factory() as session:
            repository = MemoryRepository(session)
            if command == "/memory":
                memories = await repository.list_for_user(user_id)
                response_parts: list[str] = []
                messages = list(state.get("messages", []))
                if messages:
                    response_parts.append(
                        "Recent session memory:\n" + "\n".join(render_messages(messages, limit=8))
                    )
                if memories:
                    response_parts.append(
                        "Long-term memory:\n"
                        + "\n".join(
                            f"- {memory.public_id}: [{memory.category}] {memory.memory_text}"
                            for memory in memories
                        )
                    )
                return {
                    "response": "\n\n".join(response_parts) or "No memory saved yet.",
                    "metadata": {"capability": "memory_admin"},
                }

            if command == "/forget":
                if not args:
                    return {
                        "response": "Usage: /forget <memory-id>",
                        "metadata": {"capability": "memory_admin"},
                    }
                removed = await repository.forget(user_id, args[0])
                await session.commit()
                return {
                    "response": "Memory removed." if removed else "Memory not found.",
                    "metadata": {"capability": "memory_admin"},
                }

            if command == "/resetmemory":
                await session.commit()
                await self.memory_service.reset_user_state(user_id=user_id)
                return {
                    "response": "Learned memory has been reset.",
                    "metadata": {"capability": "memory_admin"},
                }

        return {
            "response": "Memory request could not be completed.",
            "metadata": {"capability": "memory_admin"},
        }

    async def _resource_inventory(self, state: SupervisorState) -> AgentResult:
        user_id = state["user_context"]["user_id"]
        telegram_user_id = state.get("telegram_user_id")
        chat_id = state.get("chat_id")

        async with self.session_factory() as session:
            sections = await _collect_resource_inventory(
                session,
                user_id=user_id,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
            )

        return {
            "response": _format_resource_inventory(sections),
            "metadata": {
                "capability": "resource_inventory",
                "resource_counts": {section.name: section.count for section in sections},
            },
        }


async def _collect_resource_inventory(
    session,
    *,
    user_id: int,
    telegram_user_id: int | None,
    chat_id: int | None,
) -> list[ResourceSection]:
    source_repository = SourceRepository(session)
    sources = await source_repository.list_enabled(user_id)
    source_details = _source_details(sources)

    memories = await _scalars(
        session,
        select(LongTermMemory)
        .where(LongTermMemory.user_id == user_id)
        .where(LongTermMemory.status == "active")
        .order_by(LongTermMemory.updated_at.desc())
        .limit(5),
    )
    memory_details = _join_details(
        _format_count_pairs(
            await _count_by(
                session,
                LongTermMemory,
                LongTermMemory.category,
                LongTermMemory.user_id == user_id,
                LongTermMemory.status == "active",
            ),
            "categories",
        ),
        _format_recent_memories(memories),
    )

    runtime_filters = []
    if telegram_user_id is not None:
        runtime_filters.append(RuntimeRun.telegram_user_id == telegram_user_id)
    elif chat_id is not None:
        runtime_filters.append(RuntimeRun.chat_id == chat_id)
    runtime_count = await _count(session, RuntimeRun, *runtime_filters)
    runtime_details = _join_details(
        _format_count_pairs(
            await _count_by(session, RuntimeRun, RuntimeRun.status, *runtime_filters),
            "statuses",
        ),
        _format_recent_runs(
            await _scalars(
                session,
                select(RuntimeRun).where(*runtime_filters).order_by(RuntimeRun.started_at.desc()).limit(5),
            )
        ),
    )

    return [
        ResourceSection("sources", len(sources), source_details),
        ResourceSection(
            "long_term_memories",
            await _count(
                session,
                LongTermMemory,
                LongTermMemory.user_id == user_id,
                LongTermMemory.status == "active",
            ),
            memory_details,
        ),
        ResourceSection(
            "conversation_events",
            await _count(session, ConversationEvent, ConversationEvent.user_id == user_id),
            _format_count_pairs(
                await _count_by(
                    session,
                    ConversationEvent,
                    ConversationEvent.role,
                    ConversationEvent.user_id == user_id,
                ),
                "roles",
            ),
        ),
        ResourceSection(
            "memory_jobs",
            await _count(
                session,
                MemoryConsolidationJob,
                MemoryConsolidationJob.user_id == user_id,
            ),
            _format_count_pairs(
                await _count_by(
                    session,
                    MemoryConsolidationJob,
                    MemoryConsolidationJob.status,
                    MemoryConsolidationJob.user_id == user_id,
                ),
                "statuses",
            ),
        ),
        ResourceSection(
            "articles",
            await _count(session, Article),
            _join_details(
                _format_count_pairs(
                    await _count_by(session, Article, Article.category),
                    "categories",
                ),
                _format_recent_articles(
                    await _scalars(
                        session,
                        select(Article).order_by(Article.created_at.desc()).limit(5),
                    )
                ),
            ),
        ),
        ResourceSection("article_embeddings", await _count(session, ArticleEmbedding)),
        ResourceSection(
            "summaries",
            await _count(session, Summary),
            _format_count_pairs(
                await _count_by(session, Summary, Summary.summary_type),
                "types",
            ),
        ),
        ResourceSection("summary_embeddings", await _count(session, SummaryEmbedding)),
        ResourceSection(
            "market_entities",
            await _count(session, MarketEntity),
            _format_count_pairs(
                await _count_by(session, MarketEntity, MarketEntity.active),
                "active",
            ),
        ),
        ResourceSection(
            "market_mentions",
            await _count(session, MarketMention),
            _format_count_pairs(
                await _count_by(session, MarketMention, MarketMention.source_family),
                "source families",
            ),
        ),
        ResourceSection(
            "signal_snapshots",
            await _count(session, MarketSignalSnapshot),
            _format_count_pairs(
                await _count_by(session, MarketSignalSnapshot, MarketSignalSnapshot.window),
                "windows",
            ),
        ),
        ResourceSection("theme_memories", await _count(session, MarketThemeMemory)),
        ResourceSection("market_snapshots", await _count(session, MarketSnapshot)),
        ResourceSection("runtime_runs", runtime_count, runtime_details),
        ResourceSection(
            "job_runs",
            await _count(session, JobRun),
            _format_count_pairs(await _count_by(session, JobRun, JobRun.status), "statuses"),
        ),
    ]


async def _count(session, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    result = await session.execute(statement)
    return int(result.scalar_one() or 0)


async def _count_by(session, model, column, *criteria) -> list[tuple[object, int]]:
    statement = select(column, func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    result = await session.execute(statement.group_by(column).order_by(func.count().desc()))
    return [(key, int(count)) for key, count in result.all()]


async def _scalars(session, statement) -> list:
    result = await session.execute(statement)
    return list(result.scalars())


def _format_resource_inventory(sections: list[ResourceSection]) -> str:
    lines = ["Resource inventory:"]
    for section in sections:
        lines.append(f"- {section.name}: {section.count}")
        for detail in section.details:
            lines.append(f"  {detail}")
    return "\n".join(lines)


def _source_details(sources: list[Source]) -> tuple[str, ...]:
    if not sources:
        return ()
    provider_counts = Counter(source.provider for source in sources)
    category_counts = Counter(source.category for source in sources)
    unhealthy_count = sum(1 for source in sources if source.last_error)
    details = [
        _format_counter(provider_counts, "providers"),
        _format_counter(category_counts, "categories"),
    ]
    if unhealthy_count:
        details.append(f"last_error: {unhealthy_count}")
    recent = ", ".join(
        f"#{source.id} {source.name} [{source.provider}/{source.category}]"
        for source in sources[:5]
    )
    if recent:
        details.append(f"examples: {recent}")
    return tuple(details)


def _format_count_pairs(pairs: list[tuple[object, int]], label: str) -> tuple[str, ...]:
    if not pairs:
        return ()
    return (_format_counter(Counter({str(key): count for key, count in pairs}), label),)


def _format_counter(counter: Counter, label: str) -> str:
    values = ", ".join(f"{key}: {count}" for key, count in counter.most_common(6))
    return f"{label}: {values}"


def _join_details(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(detail for group in groups for detail in group)


def _format_recent_memories(memories: list[LongTermMemory]) -> tuple[str, ...]:
    if not memories:
        return ()
    values = ", ".join(
        f"{str(memory.public_id)[:8]} [{memory.category}] {_truncate(memory.memory_text, 48)}"
        for memory in memories
    )
    return (f"recent: {values}",)


def _format_recent_articles(articles: list[Article]) -> tuple[str, ...]:
    if not articles:
        return ()
    values = ", ".join(f"#{article.id} {_truncate(article.title, 56)}" for article in articles)
    return (f"recent: {values}",)


def _format_recent_runs(runs: list[RuntimeRun]) -> tuple[str, ...]:
    if not runs:
        return ()
    values = ", ".join(f"#{run.id} {run.workflow}/{run.status}" for run in runs)
    return (f"recent: {values}",)


def _truncate(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _source_config_warning(provider: str, config: dict | None) -> str:
    normalized = provider.strip().lower()
    if normalized == "rss":
        return "RSS sources use the feed URL from /addsource or config.feed_url."
    if normalized in {"twitter", "newsletter"} and not (config or {}).get("feed_url"):
        return (
            f"Set /sourceconfig <source-id> feed_url <rss-or-bridge-url> before fetching "
            f"this {normalized} source."
        )
    if normalized == "twitter":
        return "This X.com source is feed-backed; no official X API key is used."
    if normalized == "newsletter":
        return "This newsletter source is feed-backed."
    if normalized == "alpha_vantage":
        return "Set ALPHA_VANTAGE_API_KEY or config.api_key before fetching."
    if normalized == "finnhub":
        return "Set FINNHUB_API_KEY or config.api_key before fetching."
    if normalized == "polygon":
        return "Set POLYGON_API_KEY or config.api_key before fetching."
    return ""


def _parse_refresh_pipeline(args: list[str]) -> str | None:
    if not args:
        return "manual_refresh"
    normalized = args[0].strip().lower().replace("-", "_")
    aliases = {
        "all": "manual_refresh",
        "manual": "manual_refresh",
        "manual_refresh": "manual_refresh",
        "market": "market_prices",
        "price": "market_prices",
        "prices": "market_prices",
        "market_price": "market_prices",
        "market_prices": "market_prices",
        "breaking": "breaking_resources",
        "important": "breaking_resources",
        "resources": "breaking_resources",
        "breaking_resources": "breaking_resources",
        "daily": "daily_resources",
        "general": "daily_resources",
        "daily_resources": "daily_resources",
    }
    return aliases.get(normalized)


def _refresh_usage() -> str:
    return (
        "Usage: /refresh [market_prices|breaking_resources|daily_resources|all]. "
        "Aliases: prices, breaking, daily."
    )


def _format_source_pack(args: list[str] | None = None) -> str:
    category_filter = args[0].strip().lower() if args else ""
    sources = _load_source_pack()
    if category_filter:
        sources = [
            source
            for source in sources
            if str(source.get("category", "")).strip().lower() == category_filter
        ]

    if not sources:
        if category_filter:
            return f"No source-pack feeds found for category '{category_filter}'."
        return "No source-pack feeds are available."

    categories = Counter(str(source.get("category", "unknown")) for source in sources)
    lines = [
        f"Checkable source pack feeds: {len(sources)}",
        _format_counter(categories, "categories"),
    ]
    for index, source in enumerate(sources, start=1):
        lines.append(
            f"- {index}. {source['name']} [{source['category']}] "
            f"trust={source.get('trust_score', 0)}"
        )
        lines.append(f"  {source['feed_url']}")
    lines.append("Use /sourcepack <category> to filter.")
    lines.append("Use /addsource rss <feed-url>, then /sourcetest <source-id> to check one.")
    return "\n".join(lines)


def _load_source_pack() -> list[dict[str, object]]:
    try:
        payload = json.loads(SOURCE_PACK_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        source
        for source in payload
        if isinstance(source, dict)
        and isinstance(source.get("name"), str)
        and isinstance(source.get("feed_url"), str)
        and isinstance(source.get("category"), str)
    ]


def _parse_source_id(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
