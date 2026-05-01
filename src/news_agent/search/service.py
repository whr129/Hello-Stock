from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openai import APIError, AsyncOpenAI

from news_agent.settings import Settings

logger = logging.getLogger(__name__)

GENERAL_SEARCH_PROMPT = """
You answer Telegram user questions using web search.

Requirements:
- Answer the user's question directly and concisely.
- Use web search results when needed for current information.
- If the question is about stocks, securities, or investments, stay informational and do not give buy/sell advice.
- Prefer 2 to 4 short paragraphs or a very short flat list when it is clearer.
- End with a `Sources:` section only if sources are available.
- Do not invent facts or citations.
""".strip()


@dataclass(frozen=True)
class SearchSource:
    title: str
    url: str


@dataclass(frozen=True)
class SearchResult:
    query: str
    answer: str
    sources: list[SearchSource]
    metadata: dict[str, Any]


class GeneralSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = settings.general_search_model or settings.openai_model
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

    async def search(self, query: str, user_context: dict[str, Any] | None = None) -> SearchResult:
        normalized_query = query.strip()
        if not normalized_query:
            return SearchResult(
                query="",
                answer="I need a concrete question to search for.",
                sources=[],
                metadata={"status": "empty_query"},
            )
        if self.client is None:
            return SearchResult(
                query=normalized_query,
                answer="General web search is unavailable right now. Try /brief or /stocks SYMBOL.",
                sources=[],
                metadata={"status": "unavailable"},
            )

        user_location = _user_location(user_context or {})
        tools: list[dict[str, Any]] = [
            {
                "type": "web_search_preview",
                "search_context_size": "medium",
                "user_location": user_location,
            }
        ]

        try:
            response = await self.client.responses.create(
                model=self.model,
                instructions=GENERAL_SEARCH_PROMPT,
                input=normalized_query,
                tools=tools,
                include=["web_search_call.action.sources"],
                max_output_tokens=700,
                timeout=self.settings.general_search_timeout_seconds,
            )
        except APIError:
            logger.exception("general web search failed")
            return SearchResult(
                query=normalized_query,
                answer="I couldn't complete a web search right now. Try again in a moment.",
                sources=[],
                metadata={"status": "error"},
            )

        sources = _extract_sources(response, self.settings.general_search_max_sources)
        answer = _format_answer(response.output_text, sources)
        return SearchResult(
            query=normalized_query,
            answer=answer,
            sources=sources,
            metadata={"status": "ok", "source_count": len(sources)},
        )


def _extract_sources(response: Any, limit: int) -> list[SearchSource]:
    sources: list[SearchSource] = []
    seen: set[str] = set()
    for output in getattr(response, "output", []):
        if getattr(output, "type", "") != "web_search_call":
            continue
        action = getattr(output, "action", None)
        raw_sources = getattr(action, "sources", None) or []
        for item in raw_sources:
            url = getattr(item, "url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(SearchSource(title=_display_title(url), url=url))
            if len(sources) >= limit:
                return sources
    return sources


def _display_title(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return host or url


def _format_answer(answer: str, sources: list[SearchSource]) -> str:
    body = answer.strip() or "I couldn't find a reliable answer."
    if not sources:
        return body
    source_lines = "\n".join(f"- {source.title}: {source.url}" for source in sources)
    return f"{body}\n\nSources:\n{source_lines}"


def _user_location(user_context: dict[str, Any]) -> dict[str, Any]:
    timezone = user_context.get("timezone")
    region = user_context.get("local_region")
    location = {"type": "approximate", "country": "US"}
    if region:
        location["city"] = region
    if timezone:
        location["timezone"] = timezone
    return location
