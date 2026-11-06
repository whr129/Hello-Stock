from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

Effect = Literal["read", "cache", "write", "delivery"]
Status = Literal[
    "ok",
    "invalid_arguments",
    "denied",
    "unavailable",
    "timeout",
    "failed",
    "unknown_outcome",
    "budget_exceeded",
    "dependency_failed",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)


class HarnessRequest(Contract):
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    profile: str = "interactive"
    session_reference: str | None = None
    output: Literal["structured", "text"] = "structured"


class CapabilityError(Exception):
    """A deliberately public error; arbitrary exception messages are never exposed."""


class CapabilityUnavailable(CapabilityError):
    pass


class TransientCapabilityError(CapabilityError):
    pass


class UnknownOutcome(CapabilityError):
    pass


class BudgetExceeded(CapabilityError):
    pass


@dataclass
class RunBudget:
    max_calls: int
    max_turns: int
    max_tokens: int
    deadline: float
    calls: int = 0
    turns: int = 0
    tokens: int = 0
    pending_tokens: int = 0

    def reserve(self, *, calls: int = 0, turns: int = 0, tokens: int = 0) -> None:
        if min(calls, turns, tokens) < 0:
            raise ValueError("Budget reservations cannot be negative")
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("Run deadline exceeded")
        if (
            self.calls + calls > self.max_calls
            or self.turns + turns > self.max_turns
            or self.tokens + self.pending_tokens + tokens > self.max_tokens
        ):
            raise BudgetExceeded("Run budget exceeded")
        # No await between checking and reserving: all descendants share this event loop.
        self.calls += calls
        self.turns += turns
        self.tokens += tokens


class RunContext(Contract):
    principal: str
    allowed_capabilities: frozenset[str]
    budget: RunBudget = Field(exclude=True, repr=False)
    registry_snapshot: Any = Field(default=None, exclude=True, repr=False)
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_call_id: str | None = None
    user_id: int | None = None
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    depth: int = 0
    config_revision: str = ""
    registry_revision: str = ""
    allowed_effects: frozenset[Effect] = frozenset({"read", "cache", "write", "delivery"})
    data: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    def reserve_model_turn(self, *, tokens: int = 0) -> None:
        self.budget.reserve(turns=1, tokens=tokens)

    def record_tokens(self, tokens: int) -> None:
        self.budget.reserve(tokens=tokens)


Handler = Callable[[BaseModel, RunContext], Awaitable[Any]]


class CapabilitySpec(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
    description: str = ""
    input_model: type[BaseModel]
    handler: Handler = Field(exclude=True, repr=False)
    output_model: type[BaseModel] | None = None
    kind: Literal["python", "mcp", "subagent"] = "python"
    effect: Effect = "read"
    timeout_seconds: float = Field(default=15, gt=0, le=3600)
    max_attempts: int = Field(default=1, ge=1, le=3)
    idempotent: bool = False
    concurrency_group: str | None = None
    required_capability: str | None = None
    allowed_children: frozenset[str] = frozenset()
    input_schema: dict[str, Any] | None = None
    argument_validator: Callable[[dict[str, Any]], None] | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )


class ToolCall(Contract):
    id: str = Field(min_length=1)
    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    invalid_arguments: bool = False


class ToolResult(Contract):
    call_id: str
    capability_id: str
    status: Status
    data: Any = None
    error: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retryable: bool = False
    duration_ms: int = 0
    usage: dict[str, int | float] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"
