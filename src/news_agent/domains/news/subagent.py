
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
from news_agent.storage.repositories import (
    MemoryRepository,
    SourceRepository,
)


class NewsSubagent:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.ingest_registry = IngestProviderRegistry()
        self.scheduler_control = SchedulerControlService(settings)
        self.memory_service = MemoryConsolidationService(session_factory, settings)

    async def run(self, state: SupervisorState) -> AgentResult:
        capabilities = set(state.get("route", {}).get("capabilities", []))
        if "help" in capabilities:
            return {
                "response": (
                    "I can route requests between market research, stock context, "
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
        if "scheduler_admin" in capabilities:
            return await self._scheduler_admin()
        if "source_admin" in capabilities:
            return await self._source_admin(state)
        if "memory_admin" in capabilities:
            return await self._memory_admin(state)
        return {
            "response": (
                "This assistant focuses on market-impact research, source management, "
                "runtime inspection, and memory. Use /research, /candidates, /signals, "
                "/stocks, /sources, or /skills."
            ),
            "metadata": {"capability": "help"},
        }

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
                provider = self.ingest_registry.get(source.provider)
                items = provider.fetch_items(
                    source,
                    timeout_seconds=self.settings.rss_fetch_timeout_seconds,
                )
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

def _parse_source_id(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
