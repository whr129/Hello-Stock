from typing import Any, TypedDict

from news_agent.app.state import Intent as AppIntent
from news_agent.app.state import SupervisorState

Intent = AppIntent
NewsAgentState = SupervisorState
Subagent = str
ToolName = str


class SchedulerState(TypedDict, total=False):
    job_id: int
    job_type: str
    runtime_run_id: int
    active_step_id: int
    due_sources: list[dict[str, Any]]
    due_tickers: list[str]
    fetched_articles: list[dict[str, Any]]
    market_snapshots: list[dict[str, Any]]
    summaries: list[str]
    saved_articles: list[dict[str, Any]]
    errors: list[str]
    metadata: dict[str, Any]
