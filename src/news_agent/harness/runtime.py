"""Per-run execution scope, shared by model loops and nested domain agents."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from news_agent.harness.config import HarnessConfig, ProfileConfig
from news_agent.harness.contracts import RunContext, ToolCall
from news_agent.harness.executor import Executor


@dataclass(frozen=True)
class Runtime:
    executor: Executor
    context: RunContext
    config: HarnessConfig
    profile: ProfileConfig
    environ: dict[str, str] | None = field(default=None, repr=False)


_current: ContextVar[Runtime | None] = ContextVar("harness_runtime", default=None)


def current_runtime() -> Runtime | None:
    return _current.get()


@contextmanager
def runtime_scope(runtime: Runtime):
    token = _current.set(runtime)
    try:
        yield runtime
    finally:
        _current.reset(token)


def model_call(call: Any) -> ToolCall:
    invalid = False
    try:
        arguments = json.loads(call.function.arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError
    except (ValueError, TypeError):
        invalid = True
        arguments = {}
    return ToolCall(
        id=call.id, capability_id=call.function.name, arguments=arguments, invalid_arguments=invalid
    )


def result_text(result) -> str:
    if not result.ok:
        return f"Tool '{result.capability_id}' {result.status}: {result.error or 'unavailable'}"
    return (
        result.data if isinstance(result.data, str) else json.dumps(result.data, ensure_ascii=False)
    )


def record_usage(response: Any, context: RunContext) -> None:
    usage = getattr(response, "usage", None)
    tokens = getattr(usage, "total_tokens", 0)
    if isinstance(tokens, int) and tokens > 0:
        context.record_tokens(tokens)


async def execute_model_batch(runtime, calls, context, exposed):
    """Model-visible tools are an additional dispatch ceiling, including guessed aliases."""
    from news_agent.harness.contracts import ToolResult

    accepted = []
    denied = {}
    for call in calls:
        spec = context.registry_snapshot.resolve(call.capability_id)
        if spec is not None and spec.id not in exposed:
            denied[call.id] = ToolResult(
                call_id=call.id,
                capability_id=spec.id,
                status="denied",
                error="Tool is not exposed to this agent",
            )
        else:
            accepted.append(call)
    results = await runtime.executor.execute_batch(accepted, context)
    by_id = {result.call_id: result for result in results} | denied
    return [by_id[call.id] for call in calls]
