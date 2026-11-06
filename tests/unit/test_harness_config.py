import pytest
from pydantic import ValidationError

from news_agent.harness.config import HarnessConfig, MCPServerConfig, load_harness_config
from news_agent.harness.registry import CapabilityRegistry, tool_alias
from news_agent.settings import Settings


def test_configuration_precedence_profile_permissions_and_unknown_fields(tmp_path):
    path = tmp_path / "harness.toml"
    path.write_text("""schema_version = 1
[execution]
max_parallel_calls = 3
[tools.admin]
enabled = false
[profiles.interactive]
exposed_tools = ["read", "admin", "agent"]
allowed_capabilities = ["read", "admin", "agent", "leaf"]
""")
    config = load_harness_config(
        path,
        defaults={"execution": {"max_parallel_calls": 2}},
        environ={"HARNESS_MAX_PARALLEL_CALLS": "1"},
    )
    assert config.execution.max_parallel_calls == 1
    profile = config.resolve_profile("interactive", {"read", "admin", "agent"})
    assert profile.allowed_capabilities == ["agent", "read"]
    assert profile.exposed_tools == ["read", "agent"]
    with pytest.raises(ValueError, match="Unknown configured"):
        config.validate_registry({"read"})
    with pytest.raises(ValidationError):
        HarnessConfig.model_validate({"schema_version": 2})
    with pytest.raises(ValidationError):
        HarnessConfig.model_validate({"execution": {"invented_limit": 3}})


def test_agent_cycles_and_ungranted_exposure_are_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        HarnessConfig.model_validate(
            {
                "models": {"main": {"model": "test"}},
                "agents": {
                    "a": {"model": "main", "delegates": ["b"]},
                    "b": {"model": "main", "delegates": ["a"]},
                },
            }
        )
    with pytest.raises(ValidationError, match="allowlist"):
        HarnessConfig.model_validate(
            {
                "profiles": {"test": {"exposed_tools": ["admin"], "allowed_capabilities": []}},
            }
        )


def test_mcp_credentials_are_references_and_endpoint_cannot_contain_secrets():
    config = MCPServerConfig.model_validate(
        {
            "url_env": "MARKET_MCP_URL",
            "allowed_tools": ["quote"],
            "tool_policy": {"quote": {"effect": "read"}},
            "auth": {"mode": "bearer_env", "token_env": "MCP_TOKEN"},
        }
    )
    assert (
        config.endpoint({"MARKET_MCP_URL": "https://example.com/mcp"}) == "https://example.com/mcp"
    )
    with pytest.raises(ValueError):
        config.endpoint({"MARKET_MCP_URL": "https://example.com/mcp?token=secret"})
    with pytest.raises(ValueError):
        config.endpoint({})
    with pytest.raises(ValidationError):
        MCPServerConfig.model_validate({"url": "https://secret@example.com/mcp"})
    with pytest.raises(ValidationError):
        MCPServerConfig.model_validate(
            {"url": "https://example.com/mcp", "allowed_tools": ["write"]}
        )


def test_application_settings_are_validated_and_revision_is_stable():
    first = HarnessConfig.model_validate(
        {"settings": {"OPENAI_MODEL": "test", "llm_timeout_seconds": 5}}
    )
    second = HarnessConfig.model_validate(
        {"settings": {"llm_timeout_seconds": 5, "OPENAI_MODEL": "test"}}
    )
    first.validate_settings(Settings)
    assert first.revision == second.revision
    with pytest.raises(ValueError):
        HarnessConfig(settings={"invented_setting": True}).validate_settings(Settings)


def test_dotted_capability_aliases_are_bounded_and_distinct():
    assert tool_alias("web_search") == "web_search"
    assert tool_alias("news.search") != tool_alias("news_search")
    assert len(tool_alias("mcp." + "x" * 100)) <= 64
    assert CapabilityRegistry().catalog(set()) == []
