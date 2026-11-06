import os

import httpx2
import pytest
from cryptography.fernet import Fernet
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from news_agent.harness.http import BoundedTransport
from news_agent.harness.oauth import EncryptedTokenStorage


def storage(tmp_path, key, *, principal="alice", server="https://market.example/mcp", scope="read"):
    return EncryptedTokenStorage(tmp_path, key=key, principal=principal, server=server, scope=scope)


@pytest.mark.asyncio
async def test_encrypted_storage_principal_server_scope_isolation_and_local_revocation(tmp_path):
    key = Fernet.generate_key().decode()
    store = storage(tmp_path, key)
    token = OAuthToken(
        access_token="private-access", refresh_token="private-refresh", expires_in=60
    )
    await store.set_tokens(token)
    await store.set_client_info(
        OAuthClientInformationFull(client_id="client", client_secret="private-client")
    )
    encrypted = store.path.read_bytes()
    assert b"private" not in encrypted
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    assert (await storage(tmp_path, key).get_tokens()).access_token == "private-access"
    for other in (
        storage(tmp_path, key, principal="bob"),
        storage(tmp_path, key, server="other"),
        storage(tmp_path, key, scope="write"),
    ):
        assert await other.get_tokens() is None
        assert await other.get_client_info() is None
    store.revoke()
    assert await store.get_tokens() is None


@pytest.mark.asyncio
async def test_official_sdk_refreshes_expired_enrollment_and_persists_rotated_token(tmp_path):
    store = storage(tmp_path, Fernet.generate_key().decode())
    await store.set_tokens(
        OAuthToken(access_token="expired", refresh_token="renew", expires_in=0, scope="read")
    )
    await store.set_client_info(
        OAuthClientInformationFull(
            client_id="client", issuer="https://market.example", token_endpoint_auth_method="none"
        )
    )
    seen = []

    async def endpoint(request):
        seen.append(request)
        if request.url.path == "/token":
            assert b"refresh_token=renew" in request.content
            return httpx2.Response(
                200,
                json={
                    "access_token": "renewed",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "refresh_token": "rotated",
                },
            )
        assert request.headers["Authorization"] == "Bearer renewed"
        return httpx2.Response(200, json={"ok": True})

    provider = OAuthClientProvider(
        server_url="https://market.example/mcp",
        storage=store,
        client_metadata=OAuthClientMetadata(
            redirect_uris=["http://127.0.0.1/callback"], scope="read"
        ),
    )
    async with httpx2.AsyncClient(
        auth=provider, transport=httpx2.MockTransport(endpoint)
    ) as client:
        response = await client.get("https://market.example/mcp")
    assert response.status_code == 200
    assert len(seen) == 2
    assert (await store.get_tokens()).refresh_token == "rotated"


@pytest.mark.asyncio
async def test_transport_bounds_stream_before_sdk_json_parsing():
    closed = []

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 400
            yield b"y" * 400

        async def aclose(self):
            closed.append(True)

    async def endpoint(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx2.Response(200, stream=Stream())

    async with httpx2.AsyncClient(
        transport=BoundedTransport(httpx2.MockTransport(endpoint), 512)
    ) as client:
        with pytest.raises(httpx2.ReadError, match="byte limit"):
            await client.get("https://market.example/mcp")
    assert closed


@pytest.mark.asyncio
async def test_restart_preserves_discovered_authorization_server_binding(tmp_path, monkeypatch):
    from mcp.shared.auth import OAuthMetadata

    from news_agent.harness.config import MCPServerConfig
    from news_agent.harness.oauth import oauth_provider

    monkeypatch.setenv("HARNESS_OAUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config = MCPServerConfig(
        url="https://market.example/mcp",
        auth={"mode": "oauth", "storage_directory": str(tmp_path), "scope": "read"},
    )

    async def interactive_placeholder(*args):
        pass

    enrolled = oauth_provider(
        "alice",
        "market",
        config,
        redirect_handler=interactive_placeholder,
        callback_handler=interactive_placeholder,
    )
    enrolled.context.auth_server_url = "https://login.example"
    enrolled.context.oauth_metadata = OAuthMetadata(
        issuer="https://login.example",
        authorization_endpoint="https://login.example/authorize",
        token_endpoint="https://login.example/renew",
    )
    await enrolled.context.storage.set_client_info(
        OAuthClientInformationFull(client_id="client", issuer="https://login.example")
    )
    await enrolled.context.storage.set_tokens(
        OAuthToken(access_token="expired", refresh_token="renewable", expires_in=0)
    )
    restored = oauth_provider("alice", "market", config)

    async def endpoint(request):
        if request.method == "POST":
            assert str(request.url) == "https://login.example/renew"
            return httpx2.Response(
                200, json={"access_token": "valid", "token_type": "Bearer", "expires_in": 3600}
            )
        assert request.headers["Authorization"] == "Bearer valid"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(
        auth=restored, transport=httpx2.MockTransport(endpoint)
    ) as client:
        assert (await client.get("https://market.example/mcp")).status_code == 200


def test_headless_missing_enrollment_is_unavailable_without_network(tmp_path):
    from news_agent.harness.config import MCPServerConfig
    from news_agent.harness.contracts import CapabilityUnavailable
    from news_agent.harness.oauth import oauth_provider

    config = MCPServerConfig(
        url="https://market.example/mcp", auth={"mode": "oauth", "storage_directory": str(tmp_path)}
    )
    with pytest.raises(CapabilityUnavailable, match="enrollment"):
        oauth_provider(
            "alice",
            "market",
            config,
            environ={"HARNESS_OAUTH_ENCRYPTION_KEY": Fernet.generate_key().decode()},
        )
    assert list(tmp_path.iterdir()) == []
