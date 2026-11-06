"""Transport-independent capability execution for interactive and scheduled research."""

from news_agent.harness.config import ExecutionConfig, HarnessConfig, load_harness_config
from news_agent.harness.contracts import (
    CapabilityError,
    CapabilitySpec,
    CapabilityUnavailable,
    HarnessRequest,
    RunContext,
    ToolCall,
    ToolResult,
)
from news_agent.harness.executor import Executor
from news_agent.harness.registry import CapabilityRegistry

__all__ = [
    "CapabilityError",
    "CapabilityRegistry",
    "CapabilitySpec",
    "CapabilityUnavailable",
    "ExecutionConfig",
    "Executor",
    "HarnessConfig",
    "HarnessRequest",
    "RunContext",
    "ToolCall",
    "ToolResult",
    "load_harness_config",
]
