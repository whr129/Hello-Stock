from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

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
    "sourceconfig",
    "sourcefields",
    "sourcetest",
    "refresh",
    "memory",
    "forget",
    "resetmemory",
    "timezone",
    "recaptime",
    "recapoff",
    "recapstatus",
    "runtime",
    "job",
    "trace",
    "step",
    "alerts",
    "skills",
    "help",
    "general_chat",
    "unknown",
]

AgentName = Literal["news", "market", "runtime"]
Capability = Literal[
    "news_brief",
    "source_admin",
    "topic_preferences",
    "local_preferences",
    "recap_admin",
    "scheduler_admin",
    "memory_admin",
    "runtime_inspection",
    "runtime_alerts",
    "skills",
    "help",
    "market_snapshot",
    "technical_analysis",
    "watchlist_admin",
    "general_search",
]


class RouteState(TypedDict, total=False):
    agents: list[AgentName]
    capabilities: list[Capability]
    fallback_response: str | None


class UserContext(TypedDict, total=False):
    user_id: int
    local_region: str
    timezone: str
    topics: list[str]
    watched_tickers: list[str]
    short_term_memory: dict[str, Any]
    long_term_memory: list[str]


class AgentResult(TypedDict, total=False):
    response: str
    metadata: dict[str, Any]


class SupervisorState(TypedDict, total=False):
    telegram_user_id: int
    chat_id: int
    message_text: str
    command: str
    args: list[str]
    intent: Intent
    requested_symbols: list[str]
    requested_topics: list[str]
    route: RouteState
    pending_agents: list[AgentName]
    completed_agents: list[AgentName]
    messages: Annotated[list[AnyMessage], add_messages]
    user_context: UserContext
    news_result: AgentResult
    market_result: AgentResult
    runtime_result: AgentResult
    search_result: AgentResult
    runtime_run_id: int
    active_step_id: int
    reflection_attempts: int
    reflection_decision: dict[str, Any]
    reflection_notes: list[str]
    reflection_exhausted: bool
    final_response: str
    response: str
    errors: list[str]
    metadata: dict[str, Any]
