from typing import Any, Literal, TypedDict

Intent = Literal[
    "brief",
    "stocks",
    "watch",
    "unwatch",
    "topics",
    "local",
    "sources",
    "addsource",
    "removesource",
    "memory",
    "forget",
    "resetmemory",
    "help",
    "general_chat",
    "unknown",
]


class NewsAgentState(TypedDict, total=False):
    telegram_user_id: int
    chat_id: int
    message_text: str
    command: str
    args: list[str]
    intent: Intent
    user_id: int
    local_region: str
    topics: list[str]
    watched_tickers: list[str]
    short_term_memory: dict[str, Any]
    long_term_memory: list[str]
    retrieved_articles: list[dict[str, Any]]
    retrieved_summaries: list[str]
    market_context: list[dict[str, Any]]
    response: str
    errors: list[str]
    metadata: dict[str, Any]


class SchedulerState(TypedDict, total=False):
    job_id: int
    job_type: str
    due_sources: list[dict[str, Any]]
    due_tickers: list[str]
    fetched_articles: list[dict[str, Any]]
    market_snapshots: list[dict[str, Any]]
    summaries: list[str]
    saved_articles: list[dict[str, Any]]
    errors: list[str]
    metadata: dict[str, Any]
