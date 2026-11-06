import argparse
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from news_agent.alerts import cli
from news_agent.settings import Settings

SECRET = "do-not-print-test-key"


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, {"data": {"label": SECRET}}, "valid"),
        (200, {"data": {"expires_at": "2000-01-01T00:00:00Z", "label": SECRET}}, "expired"),
        (401, {"error": {"message": "expired " + SECRET}}, "unauthorized"),
        (500, {"error": SECRET}, "unavailable"),
    ],
)
def test_cli_credentials_never_print_response_or_token(monkeypatch, capsys, status, body, expected):
    response = Mock(status_code=status, json=Mock(return_value=body))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    monkeypatch.setattr(cli.httpx, "AsyncClient", Mock(return_value=client))
    monkeypatch.setattr(
        cli, "get_settings", lambda: Settings(_env_file=None, openrouter_api_key=SECRET)
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["check-credentials"])
    output = capsys.readouterr().out
    assert SECRET not in output
    assert json.loads(output)["status"] == expected
    assert error.value.code == (0 if expected == "valid" else 2)
    assert client.get.call_args.args == ("https://openrouter.ai/api/v1/key",)
    assert client.get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {SECRET}"


@pytest.mark.asyncio
async def test_credentials_network_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(cli.httpx, "AsyncClient", Mock(side_effect=RuntimeError(SECRET)))
    code, report = await cli.check_credentials(Settings(_env_file=None, openrouter_api_key=SECRET))
    assert code == 2
    assert SECRET not in json.dumps(report)


@pytest.mark.asyncio
async def test_missing_key_makes_no_http_request(monkeypatch):
    client = Mock()
    monkeypatch.setattr(cli.httpx, "AsyncClient", client)
    code, report = await cli.check_credentials(Settings(_env_file=None, openrouter_api_key=""))
    assert code == 2 and report["status"] == "missing"
    client.assert_not_called()


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"quarantined_http_status": 401}, True),
        ({"last_http_status": 401, "unauthorized_failures": 2}, True),
        ({"last_http_status": 401, "unauthorized_failures": 1}, False),
        ({"last_http_status": 403, "unauthorized_failures": 2}, False),
        ({"last_error": "401 Unauthorized"}, False),
        ({"last_http_status": "401", "unauthorized_failures": 2}, False),
    ],
)
def test_cleanup_requires_structured_confirmed_401(config, expected):
    assert cli.confirmed_unauthorized(config) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("write", [False, True])
async def test_cleanup_only_disables_confirmed_sources(write):
    bad = SimpleNamespace(enabled=True, config={"quarantined_http_status": 401})
    good = SimpleNamespace(enabled=True, config={"last_error": "401"})
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.execute.return_value = Mock(scalars=Mock(return_value=[bad, good]))
    result = await cli.cleanup_unauthorized(lambda: session, write=write)
    assert result["matched"] == 1
    assert result["disabled"] == int(write)
    assert bad.enabled is (not write)
    assert good.enabled
    assert session.commit.await_count == int(write)


@pytest.mark.asyncio
async def test_once_disabled_does_not_open_database(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(cli, "create_session_factory", factory)
    code, report = await cli.run(
        argparse.Namespace(command="once"), Settings(_env_file=None, market_alerts_enabled=False)
    )
    assert code == 2 and report["status"] == "disabled"
    factory.assert_not_called()


def test_settings_environment_precedes_dotenv(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENROUTER_API_KEY=file-key\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-key")
    settings = Settings(_env_file=path)
    assert settings.openrouter_api_key.get_secret_value() == "environment-key"


@pytest.mark.asyncio
async def test_once_closes_service_and_database_when_collection_fails(monkeypatch):
    engine = SimpleNamespace(dispose=AsyncMock())
    factory = SimpleNamespace(kw={"bind": engine})
    service = SimpleNamespace(
        aclose=AsyncMock(), process_once=AsyncMock(),
        settings=Settings(_env_file=None, market_alerts_enabled=True),
    )
    collector = SimpleNamespace(collect_once=AsyncMock(side_effect=RuntimeError("failure")))
    monkeypatch.setattr(cli, "create_session_factory", lambda settings: factory)
    monkeypatch.setattr(cli, "MarketAlertService", lambda factory, settings: service)
    monkeypatch.setattr(cli, "FastNewsCollector", lambda factory, settings: collector)
    with pytest.raises(RuntimeError):
        await cli.run(
            argparse.Namespace(command="once"), Settings(_env_file=None, market_alerts_enabled=True)
        )
    service.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()
    service.process_once.assert_not_awaited()
