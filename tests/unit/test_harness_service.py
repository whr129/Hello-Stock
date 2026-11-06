import asyncio
from types import SimpleNamespace

import pytest

from news_agent.harness import CapabilitySpec, HarnessRequest, ToolCall
from news_agent.harness.config import ToolConfig
from news_agent.harness.contracts import Contract
from news_agent.harness.runtime import execute_model_batch, model_call
from news_agent.harness.service import StockNewsHarness, effective_settings
from news_agent.settings import Settings


class Empty(Contract):
    pass


def harness(**kwargs):
    return StockNewsHarness(None, Settings(_env_file=None, openai_api_key=""), environ={}, **kwargs)


@pytest.mark.asyncio
async def test_cli_and_scheduled_profiles_share_contract_without_private_memory():
    seen = []

    async def inspect(args, context):
        seen.append(context)
        return {"principal": context.principal, "private": context.data.get("user_context")}

    service = harness(
        registrations=[CapabilitySpec(id="probe", input_model=Empty, handler=inspect)]
    )
    for profile in ("cli", "scheduled"):
        context = service.context(
            principal=profile,
            profile=profile,
            user_id=42,
            data={"user_context": {"user_id": 42, "long_term_memory": ["private"]}},
        )
        result = await service.run(HarnessRequest(operation="probe", profile=profile), context)
        assert result.ok
        assert result.data == {"principal": profile, "private": {}}
        assert seen[-1].user_id is None
        assert "news_admin" not in seen[-1].allowed_capabilities
    result = await service.run(
        HarnessRequest(operation="inspect", profile="cli"),
        service.context(principal="cli", profile="cli"),
    )
    assert set(result.data) == {
        "config_revision",
        "effective_config",
        "allowed_capabilities",
        "mcp",
    }
    assert "probe" in result.data["allowed_capabilities"]


@pytest.mark.asyncio
async def test_disabled_tool_blocks_model_guess_and_deterministic_fallback():
    service = harness()
    service.config = service.config.model_copy(
        update={"tools": {"web_search": ToolConfig(enabled=False)}}
    )
    result = await service.run(
        HarnessRequest(operation="chat", arguments={"message_text": "weather"}),
        service.context(principal="alice"),
    )
    assert "not permitted" in result.data["response"]
    async with service.scope(service.context(principal="alice")) as runtime:
        result = await execute_model_batch(
            runtime,
            [ToolCall(id="x", capability_id="stored_articles", arguments={"query": "hidden"})],
            runtime.context,
            {"runtime_agent"},
        )
        assert result[0].status == "denied"


@pytest.mark.asyncio
async def test_malformed_json_never_dispatches_even_permissive_schema():
    service = harness()
    async with service.scope(service.context(principal="alice")) as runtime:
        malformed = SimpleNamespace(
            id="bad", function=SimpleNamespace(name="runtime_agent", arguments="[")
        )
        result = await runtime.executor.execute(model_call(malformed), runtime.context)
        assert result.status == "invalid_arguments"


@pytest.mark.asyncio
async def test_direct_command_denied_before_domain_runs_and_host_narrows_profile():
    service = harness()
    context = service.context(principal="alice", allowed_capabilities={"web_search"})
    result = await service.run(
        HarnessRequest(operation="command.research", arguments={"command": "/research"}), context
    )
    assert result.status == "denied"


@pytest.mark.asyncio
async def test_configured_nested_agent_uses_model_and_shared_budget_without_leaf_deadlock():
    service = harness()
    config = service.config.model_dump()
    config["execution"]["max_parallel_calls"] = 1
    config["models"] = {"custom": {"model": "configured-model"}}
    config["agents"] = {
        "analyst": {"model": "custom", "prompt": "Configured prompt", "tools": ["runtime_agent"]}
    }
    config["profiles"]["cli"]["allowed_capabilities"].append("agent.analyst")
    service.config = type(service.config).model_validate(config)
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="research", tool_calls=[]))
                ]
            )

    service.main_agent.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    context = service.context(principal="cli", profile="cli")
    result = await asyncio.wait_for(
        service.run(
            HarnessRequest(operation="agent.analyst", arguments={"query": "test"}, profile="cli"),
            context,
        ),
        1,
    )
    assert result.ok and result.data == "research"
    assert calls[0]["model"] == "configured-model"
    assert calls[0]["messages"][0]["content"] == "Configured prompt"
    assert len(calls[0]["tools"]) == 1
    assert context.budget.turns == 1


def test_config_setting_overrides_are_validated_and_secrets_rejected():
    service = harness()
    config = service.config.model_copy(update={"settings": {"llm_timeout_seconds": 7}})
    assert effective_settings(service.settings, config, "cli").llm_timeout_seconds == 7
    config = config.model_copy(update={"settings": {"openai_api_key": "unsafe"}})
    with pytest.raises(ValueError, match="Secrets"):
        effective_settings(service.settings, config, "cli")


@pytest.mark.asyncio
async def test_persisted_trace_has_safe_revisions_status_and_no_arguments(monkeypatch):
    events = []

    class Trace:
        def __init__(self, *args):
            pass

        async def ensure_run(self, **kwargs):
            events.append(("run", kwargs))
            return 10

        async def start_step(self, **kwargs):
            events.append(("step", kwargs))
            return 20

        async def finish_step(self, *args, **kwargs):
            events.append(("finish", kwargs))

        async def finish_run(self, *args, **kwargs):
            events.append(("complete", kwargs))

    monkeypatch.setattr("news_agent.harness.tracing.RuntimeTraceService", Trace)

    async def probe(args, context):
        return "private-result"

    service = StockNewsHarness(
        object(),
        Settings(_env_file=None, openai_api_key=""),
        environ={},
        registrations=[CapabilitySpec(id="probe", input_model=Empty, handler=probe)],
    )
    result = await service.run(
        HarnessRequest(operation="probe"), service.context(principal="private-user")
    )
    assert result.ok
    assert [name for name, _ in events] == ["run", "step", "finish", "complete"]
    metadata = events[1][1]["metadata"]
    assert (
        metadata["status"] == "ok" and metadata["config_revision"] and metadata["registry_revision"]
    )
    assert "private" not in str(events)


@pytest.mark.asyncio
async def test_dotenv_model_and_mcp_secret_snapshot_is_private_and_environment_wins(
    tmp_path, monkeypatch
):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TEST_MODEL=dotenv-model\nTEST_MCP_TOKEN=private-fixture-token\n")
    monkeypatch.setenv("TEST_MODEL", "environment-model")
    base = harness()
    config = base.config.model_dump()
    config["models"] = {"custom": {"model_env": "TEST_MODEL"}}
    config["mcp"] = {
        "test": {
            "url": "https://market.example/mcp",
            "auth": {"mode": "bearer_env", "token_env": "TEST_MCP_TOKEN"},
        }
    }
    seen = []

    class Adapter:
        def __init__(self, name, config, *, principal, oauth, environ):
            seen.append(environ[config.auth.token_env])
            self.diagnostics = {"status": "ready", "tools": {}}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def capabilities(self):
            return []

    monkeypatch.setattr("news_agent.harness.service.MCPAdapter", Adapter)
    service = StockNewsHarness(
        None,
        Settings(_env_file=None, openai_api_key=""),
        config=type(base.config).model_validate(config),
        env_file=dotenv,
    )
    assert service.config.models["custom"].resolve_model(service.environ) == "environment-model"
    result = await service.run(
        HarnessRequest(operation="inspect"), service.context(principal="test")
    )
    assert seen == ["private-fixture-token"]
    assert "private-fixture-token" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_shared_alerts_toml_accepts_owned_id_and_disables_every_execution_path(tmp_path):
    from news_agent.alerts.service import MarketAlertService
    from news_agent.harness.catalog import ALERT_ASSESS_CAPABILITY

    path = tmp_path / "harness.toml"
    path.write_text("""schema_version = 1
[tools."alerts.assess"]
enabled = false
[profiles.alerts]
allowed_capabilities = ["alerts.assess"]
memory = "disabled"
[profiles.alerts.execution]
max_calls_per_run = 1
""")
    settings = Settings(_env_file=None, openai_api_key="", harness_config_file=str(path))
    shared = StockNewsHarness(None, settings, environ={})
    from news_agent.harness import HarnessConfig

    StockNewsHarness(
        None,
        settings,
        environ={},
        config=HarnessConfig(tools={"alerts.assess": ToolConfig(enabled=False)}),
    )

    class Client:
        async def assess(self, *args, **kwargs):
            pytest.fail("Disabled assessment must not make a paid Jev request")

    alerts = MarketAlertService(None, settings, client=Client())
    capability_id = ALERT_ASSESS_CAPABILITY.id
    assert not alerts.allowed
    context = alerts.executor.context(principal="service", allowed_capabilities=alerts.allowed)
    result = await alerts.executor.execute(
        ToolCall(
            id="decision", capability_id=capability_id, arguments={"state": {}, "tickers": []}
        ),
        context,
    )
    assert result.status == "denied"
    assert alerts.executor.limits.max_calls_per_run == 1
    result = await shared.run(
        HarnessRequest(
            operation=capability_id, arguments={"state": {}, "tickers": []}, profile="alerts"
        ),
        shared.context(principal="service", profile="alerts"),
    )
    assert result.status in {"denied", "unavailable"}
    assert shared.config.resolve_profile("alerts", {capability_id}).allowed_capabilities == []
    with pytest.raises(ValueError, match="Unknown configured capabilities"):
        StockNewsHarness(
            None,
            settings,
            environ={},
            config=shared.config.model_copy(
                update={"tools": {"invented.assess": ToolConfig(enabled=False)}}
            ),
        )


def test_global_then_profile_settings_preserve_environment_and_explicit_precedence(
    tmp_path, monkeypatch
):
    from news_agent.harness import HarnessConfig

    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MAIN_AGENT_MAX_TOOL_ITERATIONS", raising=False)
    config = HarnessConfig.model_validate(
        {
            "settings": {"llm_timeout_seconds": 11, "main_agent_max_tool_iterations": 2},
            "profiles": {
                "alerts": {
                    "settings": {
                        "llm_timeout_seconds": 7,
                        "main_agent_max_tool_iterations": 3,
                    }
                }
            },
        }
    )
    original = Settings(_env_file=None, openai_api_key="")
    shared = effective_settings(original, config, None)
    assert shared.llm_timeout_seconds == 11
    assert shared.main_agent_max_tool_iterations == 2
    assert shared.model_fields_set == original.model_fields_set
    profiled = effective_settings(shared, config, "alerts")
    assert profiled.llm_timeout_seconds == 7
    assert profiled.main_agent_max_tool_iterations == 3
    assert profiled.model_fields_set == original.model_fields_set

    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENAI_API_KEY=fixture-only\nLLM_TIMEOUT_SECONDS=9\n")
    environment = Settings(_env_file=dotenv, main_agent_max_tool_iterations=4)
    configured = effective_settings(effective_settings(environment, config, None), config, "alerts")
    assert configured.llm_timeout_seconds == 9
    assert configured.main_agent_max_tool_iterations == 4
    assert configured.model_fields_set == environment.model_fields_set
