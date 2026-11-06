from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from news_agent.harness.config import ExecutionConfig
from news_agent.harness.contracts import (
    BudgetExceeded,
    CapabilityError,
    CapabilitySpec,
    CapabilityUnavailable,
    RunBudget,
    RunContext,
    ToolCall,
    ToolResult,
    TransientCapabilityError,
    UnknownOutcome,
)
from news_agent.harness.registry import CapabilityRegistry, RegistrySnapshot

TraceHook = Callable[[dict[str, Any]], Awaitable[None]]
_EFFECT_LEVEL = {"read": 0, "cache": 1, "write": 2, "delivery": 3}


class Executor:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        limits: ExecutionConfig | None = None,
        trace_hook: TraceHook | None = None,
    ) -> None:
        self.registry = registry
        self.limits = limits or ExecutionConfig()
        self.trace_hook = trace_hook
        self._slots = asyncio.Semaphore(self.limits.max_parallel_calls)
        self._groups = {
            name: asyncio.Semaphore(limit) for name, limit in self.limits.concurrency_groups.items()
        }

    def context(
        self,
        *,
        principal: str,
        allowed_capabilities: set[str] | frozenset[str],
        user_id: int | None = None,
        data: dict[str, Any] | None = None,
        as_of: datetime | None = None,
        config_revision: str = "",
    ) -> RunContext:
        snapshot = self.registry.snapshot()
        return RunContext(
            principal=principal,
            allowed_capabilities=frozenset(allowed_capabilities),
            user_id=user_id,
            data=dict(data or {}),
            as_of=as_of or datetime.now(UTC),
            config_revision=config_revision,
            registry_revision=snapshot.revision,
            registry_snapshot=snapshot,
            budget=RunBudget(
                max_calls=self.limits.max_calls_per_run,
                max_turns=self.limits.max_turns,
                max_tokens=self.limits.max_tokens,
                deadline=time.monotonic() + self.limits.deadline_seconds,
            ),
        )

    async def model_completion(self, create, *, context: RunContext, **kwargs):
        """Model requests are leaf work; slots are released before delegated tools run."""
        context.reserve_model_turn()
        if not kwargs.get("tools"):
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)
        # UTF-8 bytes plus framing are a conservative input bound; reserve before dispatch
        # so concurrent model requests cannot each spend the same remaining token budget.
        input_bound = (
            len(
                json.dumps(
                    {
                        key: kwargs[key]
                        for key in ("messages", "tools", "response_format")
                        if key in kwargs
                    },
                    ensure_ascii=False,
                ).encode()
            )
            + 256
        )
        available = (
            context.budget.max_tokens - context.budget.tokens - context.budget.pending_tokens
        )
        output_limit = min(4096, available - input_bound)
        if output_limit <= 0:
            raise BudgetExceeded("Insufficient model token budget")
        reserved = input_bound + output_limit
        context.budget.pending_tokens += reserved
        kwargs["max_completion_tokens"] = output_limit
        remaining = context.budget.deadline - time.monotonic()
        try:
            async with asyncio.timeout(max(0, remaining)), self._slots:
                response = await create(**kwargs)
        except BaseException:
            context.budget.pending_tokens -= reserved
            # A provider failure may have consumed tokens even without returning usage.
            context.budget.tokens += reserved
            raise
        context.budget.pending_tokens -= reserved
        usage = getattr(getattr(response, "usage", None), "total_tokens", None)
        context.record_tokens(usage if isinstance(usage, int) and usage >= 0 else reserved)
        return response

    async def execute(self, call: ToolCall, context: RunContext) -> ToolResult:
        return (await self.execute_batch([call], context))[0]

    async def execute_batch(self, calls: list[ToolCall], context: RunContext) -> list[ToolResult]:
        if not calls:
            return []
        snapshot: RegistrySnapshot = context.registry_snapshot or self.registry.snapshot()
        prepared: dict[str, tuple[CapabilitySpec, BaseModel]] = {}
        rejected: dict[str, ToolResult] = {}
        if len({call.id for call in calls}) != len(calls):
            return [self._result(call, "invalid_arguments", "Duplicate call IDs") for call in calls]
        # Validate the whole batch before starting any handler or reserving its budget.
        for call in calls:
            if call.invalid_arguments:
                rejected[call.id] = self._result(
                    call, "invalid_arguments", "Invalid JSON arguments"
                )
                continue
            spec = snapshot.resolve(call.capability_id)
            if spec is None:
                rejected[call.id] = self._result(call, "unavailable", "Unknown capability")
                continue
            if (
                spec.id not in context.allowed_capabilities
                or spec.effect not in context.allowed_effects
                or (
                    spec.required_capability
                    and (spec.required_capability not in context.allowed_capabilities)
                )
            ):
                rejected[call.id] = self._result(call, "denied", "Capability is not permitted")
                continue
            if spec.kind == "subagent" and context.depth >= self.limits.max_agent_depth:
                rejected[call.id] = self._result(
                    call, "budget_exceeded", "Delegation depth exceeded"
                )
                continue
            try:
                if spec.argument_validator:
                    spec.argument_validator(call.arguments)
                args = spec.input_model.model_validate(call.arguments, strict=True)
            except (ValidationError, ValueError, TypeError):
                rejected[call.id] = self._result(call, "invalid_arguments", "Invalid arguments")
                continue
            prepared[call.id] = spec, args

        dependencies = {call.id: set(call.depends_on) for call in calls}
        required_dependencies = {call.id: set(call.depends_on) for call in calls}
        prior: list[str] = []
        last_write: str | None = None
        for call in calls:
            spec = snapshot.resolve(call.capability_id)
            if last_write:
                dependencies[call.id].add(last_write)
                required_dependencies[call.id].add(last_write)
            if spec and spec.effect != "read":
                dependencies[call.id].update(prior)
                last_write = call.id
            prior.append(call.id)
        if not _valid_dependencies(dependencies):
            return [
                self._result(call, "invalid_arguments", "Invalid or cyclic call dependencies")
                for call in calls
            ]

        for call in calls:
            if call.id not in rejected:
                try:
                    context.budget.reserve(calls=1)
                except BudgetExceeded:
                    rejected[call.id] = self._result(call, "budget_exceeded", "Run budget exceeded")

        tasks: dict[str, asyncio.Task[ToolResult]] = {}

        async def run(call: ToolCall) -> ToolResult:
            if call.id in rejected:
                result = rejected[call.id]
            else:
                results = await asyncio.gather(*(tasks[key] for key in dependencies[call.id]))
                if any(
                    not result.ok and result.call_id in required_dependencies[call.id]
                    for result in results
                ):
                    result = self._result(call, "dependency_failed", "A required call failed")
                else:
                    spec, args = prepared[call.id]
                    result = await self._invoke(call, spec, args, context)
            if self.trace_hook:
                try:
                    await self.trace_hook(
                        {
                            "run_id": context.run_id,
                            "parent_call_id": context.parent_call_id,
                            "call_id": result.call_id,
                            "capability_id": result.capability_id,
                            "status": result.status,
                            "duration_ms": result.duration_ms,
                            "config_revision": context.config_revision,
                            "registry_revision": context.registry_revision,
                        }
                    )
                except Exception:
                    result = result.model_copy(
                        update={
                            "warnings": [
                                *result.warnings,
                                "Capability trace could not be recorded",
                            ],
                        }
                    )
            return result

        for call in calls:
            tasks[call.id] = asyncio.create_task(run(call), name=f"capability:{call.id}")
        try:
            return list(await asyncio.gather(*(tasks[call.id] for call in calls)))
        except BaseException:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            raise

    async def _invoke(
        self,
        call: ToolCall,
        spec: CapabilitySpec,
        args: BaseModel,
        context: RunContext,
    ) -> ToolResult:
        started_at = time.monotonic()
        child = context
        if spec.kind == "subagent":
            child = context.model_copy(
                update={
                    "depth": context.depth + 1,
                    "parent_call_id": call.id,
                    "allowed_capabilities": context.allowed_capabilities & spec.allowed_children,
                    "allowed_effects": frozenset(
                        effect
                        for effect in context.allowed_effects
                        if _EFFECT_LEVEL[effect] <= _EFFECT_LEVEL[spec.effect]
                    ),
                }
            )
        began_handler = False

        async def invoke_handler() -> Any:
            nonlocal began_handler
            began_handler = True
            return await spec.handler(args, child)

        async def invoke_with_slots() -> Any:
            if spec.kind == "subagent":
                return await invoke_handler()
            async with self._slots:
                group = self._groups.get(spec.concurrency_group or "")
                if group:
                    async with group:
                        return await invoke_handler()
                return await invoke_handler()

        attempts = (
            spec.max_attempts
            if (spec.effect == "read" or (spec.effect == "cache" and spec.idempotent))
            else 1
        )
        result: ToolResult
        for attempt in range(attempts):
            remaining = context.budget.deadline - time.monotonic()
            if remaining <= 0:
                result = self._result(call, "timeout", "Run deadline exceeded")
                break
            try:
                if attempt:
                    context.budget.reserve(calls=1)
                async with asyncio.timeout(min(spec.timeout_seconds, remaining)):
                    value = await invoke_with_slots()
                supplied_result = value if isinstance(value, ToolResult) else None
                if supplied_result:
                    value = supplied_result.data
                if spec.output_model:
                    value = spec.output_model.model_validate(value)
                if isinstance(value, BaseModel):
                    value = value.model_dump(mode="json")
                if len(json.dumps(value, allow_nan=False).encode()) > self.limits.max_result_bytes:
                    raise CapabilityError("Capability result exceeded the configured limit")
                result = (
                    supplied_result.model_copy(
                        update={
                            "call_id": call.id,
                            "capability_id": spec.id,
                            "data": value,
                        }
                    )
                    if supplied_result
                    else ToolResult(
                        call_id=call.id,
                        capability_id=spec.id,
                        status="ok",
                        data=value,
                    )
                )
                break
            except TimeoutError:
                status = "unknown_outcome" if began_handler and spec.effect != "read" else "timeout"
                result = self._result(call, status, "Capability deadline exceeded")
                break
            except UnknownOutcome:
                result = self._result(call, "unknown_outcome", "Capability outcome is unknown")
                break
            except CapabilityUnavailable as exc:
                result = self._result(call, "unavailable", str(exc))
                break
            except BudgetExceeded as exc:
                result = self._result(call, "budget_exceeded", str(exc))
                break
            except TransientCapabilityError as exc:
                result = self._result(call, "failed", str(exc))
                result = result.model_copy(update={"retryable": spec.effect == "read"})
                if attempt + 1 < attempts:
                    continue
                break
            except CapabilityError as exc:
                result = self._result(call, "failed", str(exc))
                break
            except Exception:
                status = "unknown_outcome" if began_handler and spec.effect != "read" else "failed"
                result = self._result(call, status, "Capability execution failed")
                break
        return result.model_copy(
            update={"duration_ms": int((time.monotonic() - started_at) * 1000)}
        )

    @staticmethod
    def _result(call: ToolCall, status: Any, error: str) -> ToolResult:
        return ToolResult(
            call_id=call.id,
            capability_id=call.capability_id,
            status=status,
            error=error,
        )


def _valid_dependencies(dependencies: dict[str, set[str]]) -> bool:
    remaining = {key: set(value) for key, value in dependencies.items()}
    if any(not value.issubset(remaining) for value in remaining.values()):
        return False
    while remaining:
        ready = {key for key, value in remaining.items() if not value}
        if not ready:
            return False
        remaining = {key: value - ready for key, value in remaining.items() if key not in ready}
    return True
