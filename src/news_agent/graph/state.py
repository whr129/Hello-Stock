from typing import Any, TypedDict

from news_agent.app.state import Intent, SupervisorState as NewsAgentState

Subagent = str
ToolName = str


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
