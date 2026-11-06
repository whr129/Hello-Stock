"""Official SDK OAuth with encrypted, principal-scoped local enrollment storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)

from news_agent.harness.contracts import CapabilityUnavailable


class EncryptedTokenStorage:
    def __init__(self, directory, *, key: str, principal: str, server: str, scope: str):
        self._cipher = Fernet(key.encode())
        self.provider = None
        identity = json.dumps([principal, server, scope], separators=(",", ":")).encode()
        self.path = Path(directory) / (hashlib.sha256(identity).hexdigest() + ".enc")

    def _read(self):
        try:
            return json.loads(self._cipher.decrypt(self.path.read_bytes()))
        except FileNotFoundError:
            return {}
        except (InvalidToken, ValueError):
            raise CapabilityUnavailable("OAuth enrollment cannot be decrypted") from None

    def _write(self, data):
        if self.provider is not None:
            context = self.provider.context
            for name in ("oauth_metadata", "protected_resource_metadata"):
                value = getattr(context, name)
                if value is not None:
                    data[name] = value.model_dump(mode="json")
            data["auth_server_url"] = context.auth_server_url
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".oauth-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self._cipher.encrypt(json.dumps(data).encode()))
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def get_tokens(self):
        data = self._read()
        if "tokens" not in data:
            return None
        token = OAuthToken.model_validate(data["tokens"])
        if token.expires_in is not None:
            remaining = token.expires_in - int(time.time() - data["stored_at"])
            token.expires_in = max(0, remaining)
            # SDK 2.2 does not restore expiry on load. Never offer an expired access token;
            # retaining the refresh token lets its normal auth flow renew the grant.
            if remaining <= 0:
                token.access_token = ""
        return token

    async def set_tokens(self, tokens):
        self._write(
            {**self._read(), "tokens": tokens.model_dump(mode="json"), "stored_at": time.time()}
        )

    async def get_client_info(self):
        data = self._read()
        return (
            OAuthClientInformationFull.model_validate(data["client"]) if "client" in data else None
        )

    async def set_client_info(self, client_info):
        self._write({**self._read(), "client": client_info.model_dump(mode="json")})

    def revoke(self):
        """Remove this local grant; provider-side revocation remains an operator action."""
        self.path.unlink(missing_ok=True)


def oauth_storage(principal, server, config, *, environ=None):
    env = os.environ if environ is None else environ
    key = env.get(config.auth.encryption_key_env, "")
    if not key:
        raise CapabilityUnavailable("OAuth storage encryption key is missing")
    try:
        return EncryptedTokenStorage(
            config.auth.storage_directory,
            key=key,
            principal=principal,
            server=f"{server}:{config.endpoint(env)}",
            scope=config.auth.scope,
        )
    except ValueError:
        raise CapabilityUnavailable("OAuth storage encryption key is invalid") from None


def oauth_provider(
    principal, server, config, *, redirect_handler=None, callback_handler=None, environ=None
):
    storage = oauth_storage(principal, server, config, environ=environ)
    data = storage._read()
    if (not data.get("tokens") or not data.get("client")) and (
        redirect_handler is None or callback_handler is None
    ):
        raise CapabilityUnavailable("OAuth enrollment is required")
    provider = OAuthClientProvider(
        server_url=config.endpoint(environ),
        storage=storage,
        client_metadata=OAuthClientMetadata(
            client_name="Hello Stock",
            redirect_uris=[config.auth.redirect_uri],
            scope=config.auth.scope or None,
            token_endpoint_auth_method="none",
        ),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    metadata = data.get("oauth_metadata")
    if metadata:
        provider.context.oauth_metadata = OAuthMetadata.model_validate(metadata)
        provider.context.auth_server_url = data.get("auth_server_url")
    resource = data.get("protected_resource_metadata")
    if resource:
        provider.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate(
            resource
        )
    storage.provider = provider
    return provider
