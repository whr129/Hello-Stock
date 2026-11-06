import asyncio

import pytest
from pydantic import BaseModel, ConfigDict

from news_agent.harness import (
    CapabilityRegistry,
    CapabilitySpec,
    ExecutionConfig,
    Executor,
    ToolCall,
)
from news_agent.harness.contracts import BudgetExceeded, TransientCapabilityError
from news_agent.harness.registry import tool_alias


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = 0


def context(executor):
    return executor.context(principal="service", allowed_capabilities=executor.registry.ids)


@pytest.mark.asyncio
async def test_reads_overlap_and_results_retain_input_order():
    registry = CapabilityRegistry()
    started = []
    ready = asyncio.Event()
    release = asyncio.Event()
    active = 0
    peak = 0

    async def read(args, ctx):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(args.value)
        if len(started) == 2:
            ready.set()
        await release.wait()
        active -= 1
        return args.value

    registry.register(CapabilitySpec(id="read", input_model=Args, handler=read))
    executor = Executor(registry, limits=ExecutionConfig(max_parallel_calls=2))
    task = asyncio.create_task(
        executor.execute_batch(
            [ToolCall(id=str(i), capability_id="read", arguments={"value": i}) for i in range(3)],
            context(executor),
        )
    )
    await asyncio.wait_for(ready.wait(), 1)
    assert started == [0, 1]
    release.set()
    results = await task
    assert [result.data for result in results] == [0, 1, 2]
    assert peak == 2


@pytest.mark.asyncio
async def test_write_barrier_and_explicit_dependencies_preserve_committed_order():
    registry = CapabilityRegistry()
    order = []

    async def run(args, ctx):
        order.append(args.value)
        await asyncio.sleep(0)
        return list(order)

    for name, effect in (("read", "read"), ("write", "write")):
        registry.register(CapabilitySpec(id=name, input_model=Args, handler=run, effect=effect))
    executor = Executor(registry)
    results = await executor.execute_batch(
        [
            ToolCall(id="a", capability_id="read", arguments={"value": 1}),
            ToolCall(id="b", capability_id="write", arguments={"value": 2}),
            ToolCall(id="c", capability_id="read", arguments={"value": 3}),
        ],
        context(executor),
    )
    assert order == [1, 2, 3]
    assert results[2].data == [1, 2, 3]


@pytest.mark.asyncio
async def test_all_arguments_are_validated_before_handlers_and_alias_cannot_bypass_permission():
    registry = CapabilityRegistry()
    seen = []

    async def handler(args, ctx):
        seen.append(args.value)
        return args.value

    registry.register(CapabilitySpec(id="news.read", input_model=Args, handler=handler))
    executor = Executor(registry)
    denied = executor.context(principal="user", allowed_capabilities=set())
    result = await executor.execute(
        ToolCall(id="denied", capability_id=tool_alias("news.read")),
        denied,
    )
    assert result.status == "denied"
    results = await executor.execute_batch(
        [
            ToolCall(id="bad", capability_id="news.read", arguments={"value": "4"}),
            ToolCall(id="dependent", capability_id="news.read", depends_on=("bad",)),
        ],
        context(executor),
    )
    assert [result.status for result in results] == ["invalid_arguments", "dependency_failed"]
    assert seen == []


@pytest.mark.asyncio
async def test_forward_dependencies_and_cycles():
    registry = CapabilityRegistry()
    seen = []

    async def handler(args, ctx):
        seen.append(args.value)
        return args.value

    registry.register(CapabilitySpec(id="read", input_model=Args, handler=handler))
    executor = Executor(registry)
    results = await executor.execute_batch(
        [
            ToolCall(
                id="second", capability_id="read", arguments={"value": 2}, depends_on=("first",)
            ),
            ToolCall(id="first", capability_id="read", arguments={"value": 1}),
        ],
        context(executor),
    )
    assert seen == [1, 2]
    assert [result.data for result in results] == [2, 1]
    invalid = await executor.execute_batch(
        [
            ToolCall(id="a", capability_id="read", depends_on=("b",)),
            ToolCall(id="b", capability_id="read", depends_on=("a",)),
        ],
        context(executor),
    )
    assert all(result.status == "invalid_arguments" for result in invalid)


@pytest.mark.asyncio
async def test_nested_agent_releases_leaf_slot_and_shares_budget():
    registry = CapabilityRegistry()
    executor = Executor(registry, limits=ExecutionConfig(max_parallel_calls=1, max_calls_per_run=2))

    async def leaf(args, ctx):
        return {"value": args.value, "principal": ctx.principal, "user_id": ctx.user_id}

    async def agent(args, ctx):
        first = await executor.execute(ToolCall(id="child", capability_id="leaf"), ctx)
        second = await executor.execute(ToolCall(id="excess", capability_id="leaf"), ctx)
        return {"first": first.data, "second": second.status}

    registry.register(CapabilitySpec(id="leaf", input_model=Args, handler=leaf))
    registry.register(
        CapabilitySpec(
            id="agent",
            input_model=Args,
            handler=agent,
            kind="subagent",
            allowed_children={"leaf"},
        )
    )
    ctx = executor.context(principal="alice", user_id=3, allowed_capabilities=registry.ids)
    result = await asyncio.wait_for(
        executor.execute(ToolCall(id="root", capability_id="agent"), ctx), 1
    )
    assert result.ok
    assert result.data == {
        "first": {"value": 0, "principal": "alice", "user_id": 3},
        "second": "budget_exceeded",
    }
    assert ctx.budget.calls == 2


@pytest.mark.asyncio
async def test_read_agent_cannot_delegate_writes_and_children_do_not_expand_allowlist():
    registry = CapabilityRegistry()
    executor = Executor(registry)
    writes = []

    async def write(args, ctx):
        writes.append(True)

    async def agent(args, ctx):
        return (await executor.execute(ToolCall(id="nested", capability_id="write"), ctx)).status

    registry.register(CapabilitySpec(id="write", input_model=Args, handler=write, effect="write"))
    registry.register(
        CapabilitySpec(
            id="agent",
            input_model=Args,
            handler=agent,
            kind="subagent",
            allowed_children={"write"},
        )
    )
    result = await executor.execute(ToolCall(id="a", capability_id="agent"), context(executor))
    assert result.data == "denied"
    assert writes == []
    registry2 = CapabilityRegistry()
    registry2.register(CapabilitySpec(id="write", input_model=Args, handler=write, effect="write"))
    registry2.register(
        CapabilitySpec(
            id="agent",
            input_model=Args,
            handler=agent,
            kind="subagent",
            effect="write",
            allowed_children={"write"},
        )
    )
    executor.registry = registry2
    ctx = executor.context(principal="user", allowed_capabilities={"agent"})
    result = await executor.execute(ToolCall(id="a", capability_id="agent"), ctx)
    assert result.data == "denied"


@pytest.mark.asyncio
async def test_cancellation_drains_children():
    registry = CapabilityRegistry()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def handler(args, ctx):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    registry.register(CapabilitySpec(id="wait", input_model=Args, handler=handler))
    executor = Executor(registry)
    task = asyncio.create_task(
        executor.execute(ToolCall(id="a", capability_id="wait"), context(executor))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_unknown_write_is_not_retried_and_following_read_is_blocked():
    registry = CapabilityRegistry()
    calls = []

    async def write(args, ctx):
        calls.append("write")
        await asyncio.Event().wait()

    async def read(args, ctx):
        calls.append("read")

    registry.register(
        CapabilitySpec(
            id="send",
            input_model=Args,
            handler=write,
            effect="delivery",
            timeout_seconds=0.01,
            max_attempts=3,
        )
    )
    registry.register(CapabilitySpec(id="read", input_model=Args, handler=read))
    executor = Executor(registry)
    results = await executor.execute_batch(
        [
            ToolCall(id="send", capability_id="send"),
            ToolCall(id="read", capability_id="read"),
        ],
        context(executor),
    )
    assert [result.status for result in results] == ["unknown_outcome", "dependency_failed"]
    assert calls == ["write"]


@pytest.mark.asyncio
async def test_retry_budget_and_private_exception_messages():
    registry = CapabilityRegistry()
    calls = []

    async def retry(args, ctx):
        calls.append(1)
        raise TransientCapabilityError("temporarily unavailable")

    async def fail(args, ctx):
        raise RuntimeError("secret-token-must-not-escape")

    registry.register(CapabilitySpec(id="retry", input_model=Args, handler=retry, max_attempts=3))
    registry.register(CapabilitySpec(id="fail", input_model=Args, handler=fail))
    executor = Executor(registry, limits=ExecutionConfig(max_calls_per_run=2))
    result = await executor.execute(ToolCall(id="retry", capability_id="retry"), context(executor))
    assert result.status == "budget_exceeded"
    assert len(calls) == 2
    result = await executor.execute(ToolCall(id="fail", capability_id="fail"), context(executor))
    assert result.status == "failed"
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_snapshot_and_trace_failure_do_not_repeat_completed_write():
    registry = CapabilityRegistry()
    written = []

    async def write(args, ctx):
        written.append(True)
        return "done"

    async def broken_trace(event):
        assert "arguments" not in event
        raise RuntimeError("trace database unavailable")

    registry.register(CapabilitySpec(id="write", input_model=Args, handler=write, effect="write"))
    executor = Executor(registry, trace_hook=broken_trace)
    ctx = context(executor)
    registry.register(CapabilitySpec(id="new", input_model=Args, handler=write))
    unavailable = await executor.execute(ToolCall(id="new", capability_id="new"), ctx)
    assert unavailable.status == "unavailable"
    result = await executor.execute(ToolCall(id="write", capability_id="write"), ctx)
    assert result.ok and result.warnings
    assert written == [True]


def test_model_turn_and_token_budgets_are_shared():
    executor = Executor(CapabilityRegistry(), limits=ExecutionConfig(max_turns=1, max_tokens=5))
    ctx = context(executor)
    ctx.reserve_model_turn(tokens=3)
    ctx.record_tokens(2)
    with pytest.raises(BudgetExceeded):
        ctx.record_tokens(1)
    with pytest.raises(BudgetExceeded):
        ctx.reserve_model_turn()
