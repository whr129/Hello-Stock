from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

Intent = Literal[
    "sources",
    "addsource",
    "removesource",
    "sourceconfig",
    "sourcefields",
    "sourcetest",
    "sourcepack",
    "refresh",
    "memory",
    "forget",
    "resetmemory",
    "resources",
    "runtime",
    "job",
    "refreshreport",
    "trace",
    "step",
    "alerts",
    "research",
    "candidates",
    "signals",
    "researchstatus",
    "sourcehealth",
    "skills",
    "help",
    "general_chat",
    "unknown",
]

AgentName = Literal["news", "runtime", "research"]
Capability = Literal[
    "source_admin",
    "scheduler_admin",
    "memory_admin",
    "resource_inventory",
    "runtime_inspection",
    "runtime_alerts",
    "skills",
    "help",
    "market_research",
]


class RouteState(TypedDict, total=False):
    agents: list[AgentName]
    capabilities: list[Capability]
    fallback_response: str | None


class UserContext(TypedDict, total=False):
    user_id: int
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
    route: RouteState
    messages: Annotated[list[AnyMessage], add_messages]
    user_context: UserContext
    news_result: AgentResult
    runtime_result: AgentResult
    research_result: AgentResult
    main_agent_result: AgentResult
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
