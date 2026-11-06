from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import validator_for
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import RootModel

from news_agent.harness.config import MCPServerConfig
from news_agent.harness.contracts import (
    CapabilityError,
    CapabilitySpec,
    CapabilityUnavailable,
    RunContext,
    ToolResult,
    TransientCapabilityError,
    UnknownOutcome,
)
from news_agent.harness.http import BoundedTransport


class MCPArguments(RootModel[dict[str, Any]]):
    pass


def _schema_validator(schema: dict[str, Any], limit: int) -> Any:
    if len(json.dumps(schema).encode()) > limit:
        raise ValueError("MCP schema exceeds configured size")
    nodes = 0
    references: set[str] = set()

    def inspect(value: Any, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 2048 or depth > 32:
            raise ValueError("MCP schema exceeds complexity limit")
        if isinstance(value, dict):
            if "pattern" in value or "patternProperties" in value:
                raise ValueError("Remote regular-expression schemas are unsupported")
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(item, str) or not item.startswith("#/")
                ):
                    raise ValueError("Only local MCP schema references are supported")
                if key in {"$ref", "$dynamicRef", "$recursiveRef"}:
                    references.add(item)
                inspect(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                inspect(item, depth + 1)

    inspect(schema)
    for reference in references:
        target: Any = schema
        try:
            for part in reference[2:].split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                target = target[int(key)] if isinstance(target, list) else target[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError("Unresolved local MCP schema reference") from None
    validator_type = (
        validator_for(schema, default=None) if "$schema" in schema else (Draft202012Validator)
    )
    if validator_type is None:
        raise ValueError("Unsupported MCP JSON Schema dialect")
    try:
        validator_type.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Invalid MCP JSON Schema") from exc
    return validator_type(schema)


class MCPAdapter:
    """One SDK connection owned by one authenticated principal for its entire lifecycle."""

    def __init__(
        self,
        name: str,
        config: MCPServerConfig,
        *,
        principal: str,
        environ: Mapping[str, str] | None = None,
        oauth: httpx2.Auth | None = None,
        http_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.principal = principal
        self._environ = os.environ if environ is None else environ
        self._oauth = oauth
        self._http_transport = http_transport
        self._stack: AsyncExitStack | None = None
        self._client: Any = None
        self._specs: list[CapabilitySpec] = []
        self._slots = asyncio.Semaphore(config.max_parallel_calls)
        self.diagnostics: dict[str, Any] = {"status": "disconnected", "tools": {}}

    async def __aenter__(self) -> MCPAdapter:
        if self._stack is not None:
            raise RuntimeError("MCP adapter already entered")
        stack = AsyncExitStack()
        self._stack = stack
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                endpoint = self.config.endpoint(self._environ)
                headers: dict[str, str] = {}
                if self.config.auth.mode == "bearer_env":
                    token = self._environ.get(self.config.auth.token_env or "", "")
                    if not token.strip():
                        raise CapabilityUnavailable("MCP service credential is missing")
                    headers["Authorization"] = f"Bearer {token.strip()}"
                if self.config.auth.mode == "oauth" and self._oauth is None:
                    raise CapabilityUnavailable("MCP OAuth enrollment is required")
                http_client = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        headers=headers,
                        auth=self._oauth if self.config.auth.mode == "oauth" else None,
                        follow_redirects=False,
                        timeout=self.config.timeout_seconds,
                        transport=BoundedTransport(
                            self._http_transport or httpx2.AsyncHTTPTransport(),
                            self.config.max_response_bytes,
                        ),
                    )
                )
                transport = streamable_http_client(endpoint, http_client=http_client)
                self._client = await stack.enter_async_context(
                    Client(
                        transport,
                        read_timeout_seconds=self.config.timeout_seconds,
                    )
                )
                self._specs = await self._discover()
                self.diagnostics["status"] = "ready"
        except BaseException as exc:
            try:
                await stack.aclose()
            finally:
                self._stack = None
                self._client = None
                self._specs = []
            if not isinstance(exc, Exception):
                raise
            message = (
                str(exc)
                if isinstance(exc, CapabilityUnavailable)
                else ("MCP connection or discovery unavailable")
            )
            self.diagnostics.update(status="unavailable", error=message)
            if self.config.required:
                raise CapabilityUnavailable(message) from None
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._stack:
            await self._stack.__aexit__(exc_type, exc, tb)
        self._stack = None
        self._client = None
        self.diagnostics["status"] = "disconnected"

    def capabilities(self) -> list[CapabilitySpec]:
        return list(self._specs)

    async def _discover(self) -> list[CapabilitySpec]:
        specs: list[CapabilitySpec] = []
        cursor: str | None = None
        cursors: set[str] = set()
        names: set[str] = set()
        for _ in range(self.config.max_pages):
            page = await self._client.list_tools(cursor=cursor, cache_mode="refresh")
            for tool in page.tools:
                if tool.name in names:
                    raise CapabilityUnavailable("MCP catalog contains duplicate tool names")
                names.add(tool.name)
                if len(names) > self.config.max_tools:
                    raise CapabilityUnavailable("MCP catalog exceeds configured tool count")
                if tool.name not in self.config.allowed_tools:
                    continue
                try:
                    validator = _schema_validator(tool.input_schema, self.config.max_schema_bytes)
                    output = (
                        _schema_validator(tool.output_schema, self.config.max_schema_bytes)
                        if (tool.output_schema is not None)
                        else None
                    )
                    if tool.input_schema.get("type") != "object":
                        raise ValueError("MCP tool inputs must be objects")
                    policy = self.config.tool_policy[tool.name]
                    specs.append(
                        CapabilitySpec(
                            id=f"mcp.{self.name}.{tool.name}",
                            description=(tool.description or "")[:4000],
                            input_model=MCPArguments,
                            input_schema=json.loads(json.dumps(tool.input_schema)),
                            argument_validator=self._validate_arguments(validator),
                            handler=self._handler(tool.name, output),
                            kind="mcp",
                            effect=policy.effect,
                            idempotent=policy.idempotent,
                            timeout_seconds=self.config.timeout_seconds,
                            concurrency_group=f"mcp.{self.name}",
                        )
                    )
                except (ValueError, TypeError):
                    self.diagnostics["tools"][tool.name] = "unsupported_schema"
            next_cursor = page.next_cursor
            if next_cursor is None:
                missing = set(self.config.allowed_tools) - {
                    spec.id.split(".", 2)[2] for spec in specs
                }
                for name in missing:
                    self.diagnostics["tools"].setdefault(name, "unavailable")
                if missing and self.config.required:
                    raise CapabilityUnavailable("Required MCP tool is missing or unsupported")
                return specs
            if next_cursor in cursors:
                raise CapabilityUnavailable("MCP pagination cursor repeated")
            cursors.add(next_cursor)
            cursor = next_cursor
        raise CapabilityUnavailable("MCP discovery exceeded configured page count")

    def _validate_arguments(self, validator: Any) -> Callable[[dict[str, Any]], None]:
        def validate(arguments: dict[str, Any]) -> None:
            if len(json.dumps(arguments, allow_nan=False).encode()) > self.config.max_result_bytes:
                raise ValueError("MCP arguments exceed configured size")
            try:
                validator.validate(arguments)
            except Exception as exc:
                raise ValueError("Invalid MCP tool arguments") from exc

        return validate

    def _handler(self, tool_name: str, output_validator: Any) -> Any:
        async def execute(arguments: MCPArguments, context: RunContext) -> ToolResult:
            if context.principal != self.principal:
                raise CapabilityUnavailable("MCP connection belongs to another principal")
            if self._client is None:
                raise CapabilityUnavailable("MCP connection is unavailable")
            try:
                async with self._slots:
                    result = await self._client.call_tool(tool_name, arguments.root)
            except Exception:
                if self.config.tool_policy[tool_name].effect != "read":
                    raise UnknownOutcome("Remote MCP write outcome is unknown") from None
                raise TransientCapabilityError(
                    "Remote MCP transport or authorization failed"
                ) from None
            payload = {
                "server": self.name,
                "tool": tool_name,
                "structured_content": result.structured_content,
                "content": [
                    block.model_dump(mode="json", by_alias=True) for block in result.content
                ],
            }
            if len(json.dumps(payload, allow_nan=False).encode()) > self.config.max_result_bytes:
                raise CapabilityError("MCP result exceeds configured size")
            if not result.is_error and output_validator:
                try:
                    output_validator.validate(result.structured_content)
                except (ValidationError, RecursionError):
                    raise CapabilityError(
                        "MCP structured result failed schema validation"
                    ) from None
            return ToolResult(
                call_id="remote",
                capability_id=f"mcp.{self.name}.{tool_name}",
                status="failed" if result.is_error else "ok",
                data=payload,
                error="Remote MCP tool returned an error" if result.is_error else None,
            )

        return execute
