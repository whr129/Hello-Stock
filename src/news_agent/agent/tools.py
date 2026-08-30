from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.domains.news.subagent import NewsSubagent
from news_agent.memory.embeddings import EmbeddingService
from news_agent.search.service import GeneralSearchService
from news_agent.settings import Settings
from news_agent.storage.repositories import MemoryRepository

ToolExecutor = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecutor


def build_main_tools(
    session_factory: async_sessionmaker,
    settings: Settings,
    *,
    research_agent: Any,
    runtime_agent: Any,
) -> dict[str, Tool]:
    search_service = GeneralSearchService(settings)
    news_admin = NewsSubagent(session_factory, settings)
    embedding_service = EmbeddingService(settings)
    return {
        "web_search": Tool(
            name="web_search",
            description=(
                "Answer a general question using live web search with cited sources. "
                "Use for anything not covered by stored market data."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The web query."}},
                "required": ["query"],
            },
            execute=_web_search_execute(search_service),
        ),
        "research_agent": Tool(
            name="research_agent",
            description=(
                "Run stored market research for tickers and themes, including signals, "
                "evidence, and company web research."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The research question."},
                    "tickers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
            },
            execute=_research_execute(research_agent),
        ),
        "runtime_agent": Tool(
            name="runtime_agent",
            description="Inspect bot runtime state: pipeline runs, jobs, traces, and alerts.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to inspect."}},
                "required": ["query"],
            },
            execute=_runtime_execute(runtime_agent),
        ),
        "news_admin": Tool(
            name="news_admin",
            description="Manage news sources, pipeline refreshes, and stored memory.",
            parameters={
                "type": "object",
                "properties": {
                    "request": {"type": "string", "description": "The administration request."}
                },
                "required": ["request"],
            },
            execute=_news_admin_execute(news_admin),
        ),
        "memory_search": Tool(
            name="memory_search",
            description="Search the user's stored long-term memory.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to recall."}},
                "required": ["query"],
            },
            execute=_memory_search_execute(session_factory, embedding_service, settings),
        ),
    }


def _web_search_execute(search_service: GeneralSearchService) -> ToolExecutor:
    async def execute(query: str, user_context: dict[str, Any]) -> str:
        result = await search_service.search(query, user_context)
        return result.answer

    return execute


def _research_execute(research_agent: Any) -> ToolExecutor:
    async def execute(query: str, tickers: list[str] | None = None) -> str:
        state = {
            "command": "/research",
            "args": [item.upper() for item in (tickers or [])],
            "message_text": query,
        }
        return (await research_agent.run(state))["response"]

    return execute


def _runtime_execute(runtime_agent: Any) -> ToolExecutor:
    async def execute(query: str) -> str:
        state = {"command": "/runtime", "args": [], "message_text": query}
        return (await runtime_agent.run(state))["response"]

    return execute


def _news_admin_execute(news_admin: NewsSubagent) -> ToolExecutor:
    async def execute(request: str, user_context: dict[str, Any]) -> str:
        lowered = request.lower()
        if any(term in lowered for term in ("refresh", "rerun", "run the pipeline", "trigger")):
            command, args, capability = "/refresh", ["all"], "scheduler_admin"
        elif any(term in lowered for term in ("add source", "remove source", "sources", "source")):
            command, args, capability = "/sources", [], "source_admin"
        elif "memory" in lowered:
            command, args, capability = "/memory", [], "memory_admin"
        else:
            command, args, capability = "/sources", [], "source_admin"
        state = {
            "command": command,
            "args": args,
            "message_text": request,
            "route": {"capabilities": [capability]},
            "user_context": user_context,
        }
        return (await news_admin.run(state))["response"]

    return execute


def _memory_search_execute(
    session_factory: async_sessionmaker,
    embedding_service: EmbeddingService,
    settings: Settings,
) -> ToolExecutor:
    async def execute(query: str, user_id: int) -> str:
        embedding = await embedding_service.embed_text(query)
        async with session_factory() as session:
            memories = await MemoryRepository(session).semantic_search_text(
                user_id=user_id,
                query_embedding=embedding,
                limit=settings.long_term_memory_top_k,
            )
        if not memories:
            return "No relevant stored memory was found."
        return "Relevant stored memory:\n- " + "\n- ".join(memories)

    return execute
