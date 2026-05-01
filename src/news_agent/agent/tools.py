from dataclasses import dataclass, field
from typing import Any

from news_agent.domains.market.subagent import requested_tickers as _requested_tickers


@dataclass(frozen=True)
class ToolExecution:
    response: str | None = None
    updates: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Compatibility shim kept for inactive legacy imports."""

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def run(self, tool_name: str, state: dict[str, Any]) -> ToolExecution:
        del tool_name, state
        return ToolExecution()


__all__ = ["ToolExecution", "ToolRegistry", "_requested_tickers"]
