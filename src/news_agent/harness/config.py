from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from news_agent.harness.contracts import Contract, Effect


class ExecutionConfig(Contract):
    max_parallel_calls: int = Field(default=4, ge=1, le=100)
    max_calls_per_run: int = Field(default=20, ge=1, le=10000)
    max_agent_depth: int = Field(default=2, ge=0, le=10)
    max_turns: int = Field(default=10, ge=1, le=1000)
    max_tokens: int = Field(default=32000, ge=1)
    deadline_seconds: float = Field(default=90, gt=0, le=3600)
    max_result_bytes: int = Field(default=262144, ge=256, le=16777216)
    concurrency_groups: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_groups(self) -> ExecutionConfig:
        if any(value < 1 or value > 100 for value in self.concurrency_groups.values()):
            raise ValueError("Concurrency group limits must be between 1 and 100")
        return self


class ModelConfig(Contract):
    driver: Literal["openai"] = "openai"
    model: str | None = None
    model_env: str | None = None
    temperature: float = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def valid_reference(self):
        if bool(self.model) == bool(self.model_env):
            raise ValueError("Specify exactly one model or model environment reference")
        if self.model_env and not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.model_env):
            raise ValueError("model_env must name an environment variable")
        return self

    def resolve_model(self, environ: Mapping[str, str] | None = None) -> str:
        env = os.environ if environ is None else environ
        value = env.get(self.model_env, "") if self.model_env else self.model
        if not value:
            raise ValueError("Configured model is unavailable")
        return value


class AgentConfig(Contract):
    model: str
    prompt: str | None = None
    tools: list[str] = Field(default_factory=list)
    delegates: list[str] = Field(default_factory=list)


class ToolConfig(Contract):
    enabled: bool = True
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    max_attempts: int | None = Field(default=None, ge=1, le=3)


class ProfileConfig(Contract):
    exposed_tools: list[str] = Field(default_factory=list)
    allowed_capabilities: list[str] = Field(default_factory=list)
    memory: Literal["enabled", "disabled"] = "enabled"
    settings: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionConfig | None = None

    @model_validator(mode="after")
    def tools_are_allowed(self) -> ProfileConfig:
        if not set(self.exposed_tools).issubset(self.allowed_capabilities):
            raise ValueError("Exposed tools must belong to the execution allowlist")
        return self


class MCPAuthConfig(Contract):
    mode: Literal["none", "bearer_env", "oauth"] = "none"
    token_env: str | None = None
    encryption_key_env: str = "HARNESS_OAUTH_ENCRYPTION_KEY"
    storage_directory: str = ".state/mcp-oauth"
    scope: str = ""
    redirect_uri: str = "http://127.0.0.1:8766/callback"

    @model_validator(mode="after")
    def valid_token_reference(self) -> MCPAuthConfig:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.encryption_key_env):
            raise ValueError("OAuth encryption key must be an environment reference")
        if self.mode == "bearer_env" and not self.token_env:
            raise ValueError("Bearer authentication requires token_env")
        if self.token_env and not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.token_env):
            raise ValueError("token_env must name an environment variable")
        if self.mode != "bearer_env" and self.token_env:
            raise ValueError("token_env is only valid for bearer authentication")
        return self


class MCPToolPolicy(Contract):
    effect: Effect
    idempotent: bool = False


class MCPServerConfig(Contract):
    transport: Literal["streamable_http"] = "streamable_http"
    url: str | None = None
    url_env: str | None = None
    required: bool = False
    allowed_tools: list[str] = Field(default_factory=list)
    tool_policy: dict[str, MCPToolPolicy] = Field(default_factory=dict)
    auth: MCPAuthConfig = Field(default_factory=MCPAuthConfig)
    max_parallel_calls: int = Field(default=2, ge=1, le=100)
    timeout_seconds: float = Field(default=15, gt=0, le=300)
    max_tools: int = Field(default=100, ge=1, le=1000)
    max_pages: int = Field(default=20, ge=1, le=100)
    max_response_bytes: int = Field(default=1048576, ge=512, le=16777216)
    max_schema_bytes: int = Field(default=32768, ge=128, le=262144)
    max_result_bytes: int = Field(default=262144, ge=256, le=16777216)

    @model_validator(mode="after")
    def valid_endpoint_and_policy(self) -> MCPServerConfig:
        if bool(self.url) == bool(self.url_env):
            raise ValueError("Specify exactly one MCP URL or URL environment reference")
        if self.url_env and not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.url_env):
            raise ValueError("url_env must name an environment variable")
        if set(self.allowed_tools) != set(self.tool_policy):
            raise ValueError("Every allowed MCP tool needs exactly one trusted effect policy")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("Duplicate MCP tool allowlist entries")
        if self.url:
            self._validate_url(self.url)
        return self

    @staticmethod
    def _validate_url(value: str) -> str:
        parsed = urlsplit(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and local))
        ):
            raise ValueError("MCP requires an HTTPS endpoint without credentials or query values")
        return value

    def endpoint(self, environ: Mapping[str, str] | None = None) -> str:
        env = os.environ if environ is None else environ
        value = env.get(self.url_env, "") if self.url_env else self.url
        if not value:
            raise ValueError("MCP endpoint environment variable is missing")
        return self._validate_url(value)


class HarnessConfig(Contract):
    schema_version: Literal[1] = 1
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    mcp: dict[str, MCPServerConfig] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_agent_graph(self) -> HarnessConfig:
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise ValueError("Subagent delegation contains a cycle")
            if name in visited:
                return
            if name not in self.agents:
                raise ValueError(f"Unknown delegated agent: {name}")
            agent = self.agents[name]
            if agent.model not in self.models:
                raise ValueError(f"Unknown model registration: {agent.model}")
            visiting.add(name)
            for child in agent.delegates:
                visit(child)
            visiting.remove(name)
            visited.add(name)

        for name in self.agents:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,40}", name):
                raise ValueError("Invalid agent name")
            if set(self.agents[name].delegates) & {"main", "research"}:
                raise ValueError("Built-in routing agents cannot be delegation targets")
            visit(name)
        for name in self.mcp:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,40}", name):
                raise ValueError("Invalid MCP connection name")
        return self

    def validate_registry(self, known_ids: set[str] | frozenset[str]) -> None:
        referenced = set(self.tools)
        for profile in self.profiles.values():
            referenced.update(profile.allowed_capabilities)
        for agent in self.agents.values():
            referenced.update(agent.tools)
        unknown = referenced - set(known_ids)
        if unknown:
            raise ValueError(f"Unknown configured capabilities: {', '.join(sorted(unknown))}")

    def resolve_profile(
        self, name: str, host_allowlist: set[str] | frozenset[str]
    ) -> ProfileConfig:
        if name not in self.profiles:
            raise ValueError(f"Unknown harness profile: {name}")
        profile = self.profiles[name]
        allowed = set(profile.allowed_capabilities) & set(host_allowlist)
        allowed -= {name for name, tool in self.tools.items() if not tool.enabled}
        return profile.model_copy(
            update={
                "allowed_capabilities": sorted(allowed),
                "exposed_tools": [name for name in profile.exposed_tools if name in allowed],
            }
        )

    def validate_settings(self, settings_model: type[Any]) -> None:
        known = set(settings_model.model_fields)
        known.update(field.alias for field in settings_model.model_fields.values() if field.alias)
        configured = set(self.settings)
        for profile in self.profiles.values():
            configured.update(profile.settings)
        if configured - known:
            raise ValueError("Unknown application setting in harness configuration")
        aliases = {field.alias or name: name for name, field in settings_model.model_fields.items()}
        names = {aliases.get(name, name) for name in configured}
        if any(
            name == "database_url" or name.endswith(("_api_key", "_token", "_password", "_secret"))
            for name in names
        ):
            raise ValueError("Secrets must be supplied through environment variables")

    @property
    def revision(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def _merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in updates.items():
        result[key] = (
            _merge(result[key], value)
            if (isinstance(result.get(key), dict) and isinstance(value, dict))
            else value
        )
    return result


def load_harness_config(
    path: str | Path | None = None,
    *,
    defaults: dict[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> HarnessConfig:
    data = dict(defaults or {})
    if path is not None:
        with Path(path).open("rb") as stream:
            data = _merge(data, tomllib.load(stream))
    env = os.environ if environ is None else environ
    execution = dict(data.get("execution") or {})
    for field in ExecutionConfig.model_fields:
        env_key = f"HARNESS_{field.upper()}"
        if env_key in env:
            execution[field] = json.loads(env[env_key])
    data["execution"] = execution
    return HarnessConfig.model_validate(data)
