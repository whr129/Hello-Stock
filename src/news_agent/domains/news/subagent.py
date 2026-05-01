from typing import Any

from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.agent.chains import build_brief_response
from news_agent.agent.ranking import rank_articles
from news_agent.app.state import AgentResult, SupervisorState
from news_agent.agent.router import skills_response
from news_agent.ingestion.providers import IngestProviderRegistry
from news_agent.scheduler.service import (
    SchedulerControlService,
    parse_config_value,
    validate_delivery_time,
    validate_timezone,
)
from news_agent.settings import Settings
from news_agent.storage.repositories import (
    MemoryRepository,
    PreferenceRepository,
    SourceRepository,
    UserRepository,
)
from news_agent.storage.retrieval import RetrievalService


class NewsSubagent:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.ingest_registry = IngestProviderRegistry()
        self.scheduler_control = SchedulerControlService(settings)

    async def run(self, state: SupervisorState) -> AgentResult:
        capabilities = set(state.get("route", {}).get("capabilities", []))
        if "help" in capabilities:
            return {
                "response": (
                    "I can route requests between news coverage, market analysis, and general web search. "
                    "Try /skills for the full command list, or ask a general question directly."
                ),
                "metadata": {"capability": "help"},
            }
        if "skills" in capabilities:
            return {
                "response": skills_response(),
                "metadata": {"capability": "skills"},
            }
        if "scheduler_admin" in capabilities:
            return await self._scheduler_admin()
        if "source_admin" in capabilities:
            return await self._source_admin(state)
        if "topic_preferences" in capabilities:
            return await self._topic_preferences(state)
        if "local_preferences" in capabilities:
            return await self._local_preferences(state)
        if "recap_admin" in capabilities:
            return await self._recap_admin(state)
        if "memory_admin" in capabilities:
            return await self._memory_admin(state)
        return await self._news_brief(state)

    async def _scheduler_admin(self) -> AgentResult:
        if not await self.scheduler_control.can_start_refresh():
            return {
                "response": "A refresh job is already running. Try again in a moment.",
                "metadata": {"capability": "scheduler_admin"},
            }
        summary = await self.scheduler_control.run_refresh()
        return {
            "response": self.scheduler_control.format_refresh_summary(summary),
            "metadata": {"capability": "scheduler_admin"},
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
                            "/addsource twitter @openai"
                        ),
                        "metadata": {"capability": "source_admin"},
                    }
                provider = args[0].lower()
                external_account = args[1]
                if provider not in {"rss", "twitter", "newsletter"}:
                    return {
                        "response": "Supported source providers: rss, twitter, newsletter.",
                        "metadata": {"capability": "source_admin"},
                    }
                config = {"feed_url": external_account} if provider == "rss" else {}
                source = await repository.add_source(
                    name=external_account,
                    provider=provider,
                    external_account=external_account,
                    owner_user_id=user_id,
                    config=config,
                    fetch_mode="rss" if provider == "rss" else None,
                )
                await session.commit()
                return {
                    "response": (
                        f"Added source {source.name} [{source.provider}] "
                        f"{source.external_account}. "
                        "Use /sourceconfig if you need extra provider settings."
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
                return {
                    "response": f"Updated source {source.id} config {key}={value}.",
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
                source = await repository.update_field_mapping(source_id, args[1], " ".join(args[2:]))
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
                provider = self.ingest_registry.get(source.provider)
                items = provider.fetch_items(source, timeout_seconds=self.settings.rss_fetch_timeout_seconds)
                preview = "\n".join(f"- {item.title}" for item in items[:3]) or "- no items"
                return {
                    "response": (
                        f"Source test completed for {source.name}.\n"
                        f"- Items fetched: {len(items)}\n"
                        f"- Preview:\n{preview}"
                    ),
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

    async def _topic_preferences(self, state: SupervisorState) -> AgentResult:
        args = state.get("args", [])
        async with self.session_factory() as session:
            preference = await PreferenceRepository(session).set_topics(
                state["user_context"]["user_id"],
                args,
            )
            await session.commit()
        state["user_context"]["topics"] = preference.topics
        return {
            "response": f"Topics updated: {', '.join(preference.topics)}",
            "metadata": {"capability": "topic_preferences"},
        }

    async def _local_preferences(self, state: SupervisorState) -> AgentResult:
        args = state.get("args", [])
        if not args:
            return {
                "response": "Usage: /local Waterloo",
                "metadata": {"capability": "local_preferences"},
            }

        local_region = " ".join(args)
        async with self.session_factory() as session:
            user = await UserRepository(session, self.settings).set_local_region(
                state["user_context"]["user_id"],
                local_region,
            )
            await session.commit()

        state["user_context"]["local_region"] = user.local_region if user else local_region
        return {
            "response": f"Local region updated: {local_region}",
            "metadata": {"capability": "local_preferences"},
        }

    async def _recap_admin(self, state: SupervisorState) -> AgentResult:
        command = state.get("command", "")
        args = state.get("args", [])
        user_id = state["user_context"]["user_id"]
        async with self.session_factory() as session:
            preference_repo = PreferenceRepository(session)
            user_repo = UserRepository(session, self.settings)
            user = await user_repo.get_or_create_user(state["telegram_user_id"])
            preference = await preference_repo.get_for_user(user_id)

            if command == "/timezone":
                if not args:
                    return {
                        "response": "Usage: /timezone Area/City",
                        "metadata": {"capability": "recap_admin"},
                    }
                timezone_value = " ".join(args)
                try:
                    validate_timezone(timezone_value)
                except ZoneInfoNotFoundError:
                    return {
                        "response": "Invalid timezone. Use an IANA timezone like America/Toronto.",
                        "metadata": {"capability": "recap_admin"},
                    }
                user = await user_repo.set_timezone(user.id, timezone_value)
                await session.commit()
                return {
                    "response": f"Timezone updated: {user.timezone if user else timezone_value}",
                    "metadata": {"capability": "recap_admin"},
                }

            if command == "/recaptime":
                if not args:
                    return {
                        "response": "Usage: /recaptime HH:MM",
                        "metadata": {"capability": "recap_admin"},
                    }
                try:
                    delivery_time = validate_delivery_time(args[0])
                except ValueError:
                    return {
                        "response": "Invalid time. Use 24-hour format HH:MM.",
                        "metadata": {"capability": "recap_admin"},
                    }
                preference = await preference_repo.set_delivery_time(user_id, delivery_time)
                await session.commit()
                return {
                    "response": f"Daily recap time updated: {preference.delivery_time} ({user.timezone})",
                    "metadata": {"capability": "recap_admin"},
                }

            if command == "/recapoff":
                await preference_repo.clear_delivery_time(user_id)
                await session.commit()
                return {
                    "response": "Daily recap disabled.",
                    "metadata": {"capability": "recap_admin"},
                }

            if command == "/recapstatus":
                status = preference.delivery_time or "disabled"
                return {
                    "response": f"Recap status: {status}\nTimezone: {user.timezone}",
                    "metadata": {"capability": "recap_admin"},
                }

        return {
            "response": "Recap preferences request could not be completed.",
            "metadata": {"capability": "recap_admin"},
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
                messages = state["user_context"].get("short_term_memory", {}).get("messages", [])[-8:]
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
                await repository.reset_learned(user_id)
                await session.commit()
                return {
                    "response": "Learned memory has been reset.",
                    "metadata": {"capability": "memory_admin"},
                }

        return {
            "response": "Memory request could not be completed.",
            "metadata": {"capability": "memory_admin"},
        }

    async def _news_brief(self, state: SupervisorState) -> AgentResult:
        user_context = state["user_context"]
        requested_symbols = set(state.get("requested_symbols", []))
        requested_symbols.update(user_context.get("watched_tickers", []))

        async with self.session_factory() as session:
            context = await RetrievalService(session).retrieve_for_brief(
                user_id=user_context["user_id"],
                topics=user_context.get("topics", []),
                tickers=sorted(requested_symbols),
                article_max_age_hours=self.settings.news_freshness_hours,
                summary_max_age_hours=self.settings.summary_freshness_hours,
                snapshot_max_age_minutes=self.settings.snapshot_freshness_minutes,
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
        ranked_articles = rank_articles(
            articles,
            user_context.get("topics", []),
            user_context.get("watched_tickers", []),
            user_context.get("local_region"),
        )
        response = build_brief_response(
            ranked_articles,
            [summary.text for summary in context.summaries],
            [
                {
                    "symbol": snapshot.symbol,
                    "price": snapshot.price,
                    "percent_change": snapshot.percent_change,
                    "indicators": snapshot.indicators,
                }
                for snapshot in context.market_snapshots
            ],
            user_context.get("local_region", self.settings.default_local_region),
        )
        return {
            "response": response,
            "metadata": {
                "capability": "news_brief",
                "article_count": len(ranked_articles),
                "summary_count": len(context.summaries),
                "needs_search_fallback": not ranked_articles and not context.summaries,
            },
        }


def _parse_source_id(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
