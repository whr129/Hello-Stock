import asyncio
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp.server import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import BaseModel

from news_agent.harness import CapabilityRegistry, CapabilitySpec, Executor, ToolCall
from news_agent.harness.config import MCPServerConfig
from news_agent.harness.contracts import CapabilityUnavailable
from news_agent.harness.mcp import MCPAdapter

INPUT = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}
OUTPUT = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


def config(**overrides):
    return MCPServerConfig.model_validate(
        {
            "url": "http://127.0.0.1:8765/mcp",
            "allowed_tools": ["quote"],
            "tool_policy": {"quote": {"effect": "read"}},
            "timeout_seconds": 2,
            **overrides,
        }
    )


@asynccontextmanager
async def server(
    *, schema=None, output=None, is_error=False, repeat_cursor=False, require_token=None
):
    calls = []
    pages = []
    tools = [
        Tool(name="hidden", input_schema=INPUT),
        Tool(name="quote", input_schema=INPUT if schema is None else schema, output_schema=OUTPUT),
    ]

    async def list_tools(ctx, params):
        cursor = params.cursor if params else None
        pages.append(cursor)
        if cursor is None:
            return ListToolsResult(tools=tools[:1], next_cursor="next")
        return ListToolsResult(tools=tools[1:], next_cursor="next" if repeat_cursor else None)

    async def call_tool(ctx, params):
        calls.append((params.name, params.arguments))
        value = {"value": params.arguments["value"] + 1} if output is None else output
        return CallToolResult(
            structured_content=value,
            content=[TextContent(type="text", text="source supplied content")],
            is_error=is_error,
        )

    instance = Server("fixture", on_list_tools=list_tools, on_call_tool=call_tool)
    app = instance.streamable_http_app(json_response=True, stateless_http=True)

    async def authenticated(scope, receive, send):
        if scope["type"] == "http" and require_token is not None:
            headers = dict(scope["headers"])
            if headers.get(b"authorization") != f"Bearer {require_token}".encode():
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await app(scope, receive, send)

    async with app.router.lifespan_context(app):
        yield httpx2.ASGITransport(app=authenticated), calls, pages


@pytest.mark.asyncio
async def test_real_sdk_http_pagination_local_remote_batch_and_shutdown():
    class Args(BaseModel):
        value: int

    async def local(args, ctx):
        return args.value * 2

    async with server(require_token="fixture-token") as (transport, calls, pages):
        adapter = MCPAdapter(
            "market",
            config(auth={"mode": "bearer_env", "token_env": "TEST_MCP_TOKEN"}),
            principal="service",
            environ={"TEST_MCP_TOKEN": "fixture-token"},
            http_transport=transport,
        )
        async with adapter:
            assert adapter.diagnostics["status"] == "ready"
            assert pages == [None, "next"]
            registry = CapabilityRegistry()
            registry.register_many(adapter.capabilities())
            registry.register(CapabilitySpec(id="local", input_model=Args, handler=local))
            assert registry.ids == {"local", "mcp.market.quote"}
            executor = Executor(registry)
            context = executor.context(principal="service", allowed_capabilities=registry.ids)
            results = await executor.execute_batch(
                [
                    ToolCall(id="remote", capability_id="mcp.market.quote", arguments={"value": 4}),
                    ToolCall(id="local", capability_id="local", arguments={"value": 4}),
                ],
                context,
            )
            assert [result.call_id for result in results] == ["remote", "local"]
            assert results[0].ok and results[0].data["structured_content"] == {"value": 5}
            assert results[0].data["server"] == "market"
            assert results[1].data == 8
            assert calls == [("quote", {"value": 4})]
        closed = await executor.execute(
            ToolCall(id="closed", capability_id="mcp.market.quote", arguments={"value": 4}),
            context,
        )
        assert closed.status == "unavailable"


@pytest.mark.asyncio
async def test_dynamic_schema_validation_and_principal_isolation_prevent_remote_calls():
    async with server() as (transport, calls, _):
        async with MCPAdapter(
            "market", config(), principal="alice", http_transport=transport
        ) as adapter:
            registry = CapabilityRegistry()
            registry.register_many(adapter.capabilities())
            executor = Executor(registry)
            alice = executor.context(principal="alice", allowed_capabilities=registry.ids)
            bad = await executor.execute(
                ToolCall(id="bad", capability_id="mcp.market.quote", arguments={"value": "4"}),
                alice,
            )
            assert bad.status == "invalid_arguments"
            bob = executor.context(principal="bob", allowed_capabilities=registry.ids)
            denied = await executor.execute(
                ToolCall(id="bob", capability_id="mcp.market.quote", arguments={"value": 4}),
                bob,
            )
            assert denied.status == "unavailable"
            assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output,is_error,expected",
    [
        ({"value": 4}, True, "failed"),
        ({"value": "invalid"}, False, "failed"),
        ({"value": "x" * 1000}, False, "failed"),
    ],
)
async def test_mcp_tool_errors_bad_outputs_and_size_limits(output, is_error, expected):
    async with server(output=output, is_error=is_error) as (transport, _, _):
        async with MCPAdapter(
            "market",
            config(max_result_bytes=512),
            principal="service",
            http_transport=transport,
        ) as adapter:
            registry = CapabilityRegistry()
            registry.register_many(adapter.capabilities())
            executor = Executor(registry)
            result = await executor.execute(
                ToolCall(id="call", capability_id="mcp.market.quote", arguments={"value": 4}),
                executor.context(principal="service", allowed_capabilities=registry.ids),
            )
            assert result.status == expected
            if is_error:
                assert result.data["content"][0]["text"] == "source supplied content"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "$ref": "https://external.invalid/schema.json"},
        {"type": "object", "$ref": "#/$defs/absent"},
        {"type": "object", "properties": {"x": {"type": "string", "pattern": "(a+)+$"}}},
        {"type": "object", "$schema": "https://unknown.invalid/dialect"},
    ],
)
async def test_untrusted_schema_is_disabled_without_fetching_references(schema):
    async with server(schema=schema) as (transport, calls, _):
        async with MCPAdapter(
            "market", config(), principal="service", http_transport=transport
        ) as adapter:
            assert adapter.capabilities() == []
            assert adapter.diagnostics["tools"]["quote"] == "unsupported_schema"
            assert calls == []


@pytest.mark.asyncio
async def test_missing_auth_optional_failure_and_required_readiness():
    async with MCPAdapter(
        "market",
        config(auth={"mode": "bearer_env", "token_env": "MCP_TOKEN"}),
        principal="service",
        environ={},
    ) as adapter:
        assert adapter.diagnostics["status"] == "unavailable"
        assert adapter.capabilities() == []
    with pytest.raises(CapabilityUnavailable, match="enrollment"):
        async with MCPAdapter(
            "market",
            config(required=True, auth={"mode": "oauth"}),
            principal="service",
        ):
            pass


@pytest.mark.asyncio
async def test_unauthorized_and_repeated_cursor_do_not_leak_credentials():
    async with server(require_token="correct") as (transport, _, _):
        async with MCPAdapter(
            "market",
            config(auth={"mode": "bearer_env", "token_env": "MCP_TOKEN"}),
            principal="service",
            environ={"MCP_TOKEN": "private-secret"},
            http_transport=transport,
        ) as adapter:
            assert adapter.diagnostics["status"] == "unavailable"
            assert "private-secret" not in str(adapter.diagnostics)
    async with server(repeat_cursor=True) as (transport, _, _):
        async with MCPAdapter(
            "market", config(), principal="service", http_transport=transport
        ) as adapter:
            assert adapter.diagnostics["status"] == "unavailable"


@pytest.mark.asyncio
async def test_mcp_deadline_cancels_local_request_task():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def waiting(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    adapter = MCPAdapter(
        "market",
        config(timeout_seconds=0.01),
        principal="service",
        http_transport=httpx2.MockTransport(waiting),
    )
    async with adapter:
        assert adapter.diagnostics["status"] == "unavailable"
    assert started.is_set() and cancelled.is_set()


@pytest.mark.asyncio
async def test_cancellation_during_discovery_closes_sdk_and_transport():
    discovery_started = asyncio.Event()
    discovery_finished = asyncio.Event()
    transport_closed = asyncio.Event()

    async def list_tools(ctx, params):
        discovery_started.set()
        try:
            await asyncio.Future()
        finally:
            discovery_finished.set()

    app = Server("cancel-fixture", on_list_tools=list_tools).streamable_http_app(
        json_response=True,
        stateless_http=True,
    )

    class Transport(httpx2.ASGITransport):
        async def aclose(self):
            transport_closed.set()
            await super().aclose()

    async with app.router.lifespan_context(app):
        baseline = asyncio.all_tasks()
        adapter = MCPAdapter(
            "market", config(), principal="fixture", environ={}, http_transport=Transport(app=app)
        )
        task = asyncio.create_task(adapter.__aenter__())
        await asyncio.wait_for(discovery_started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(discovery_finished.wait(), 1)
        assert transport_closed.is_set()
        assert adapter._client is None and adapter._stack is None
        await asyncio.sleep(0)
        assert not (asyncio.all_tasks() - baseline)
