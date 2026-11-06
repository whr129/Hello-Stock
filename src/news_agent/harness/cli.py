"""Run the same research capabilities without a Telegram identity or persistent memory."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import webbrowser
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from mcp.shared.auth import AuthorizationCodeResult

from news_agent.harness.contracts import HarnessRequest
from news_agent.harness.mcp import MCPAdapter
from news_agent.harness.oauth import oauth_provider, oauth_storage
from news_agent.harness.service import StockNewsHarness
from news_agent.settings import Settings
from news_agent.storage.database import create_session_factory


async def run_cli(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.config:
        settings = settings.model_copy(update={"harness_config_file": args.config})
    factory = (
        None
        if args.operation in {"oauth-enroll", "oauth-revoke"}
        else create_session_factory(settings)
    )
    harness = StockNewsHarness(factory, settings)
    if args.operation in {"oauth-enroll", "oauth-revoke"}:
        if not args.principal or not args.server or args.server not in harness.config.mcp:
            raise ValueError("OAuth setup requires an explicit principal and configured server")
        server = harness.config.mcp[args.server]
        if server.auth.mode != "oauth":
            raise ValueError("Selected server is not configured for OAuth")
        if args.operation == "oauth-revoke":
            oauth_storage(args.principal, args.server, server, environ=harness.environ).revoke()
            print("Local OAuth grant removed. Revoke provider consent in its account settings.")
            return 0

        async def redirect(url):
            webbrowser.open(url)
            print("Complete authorization in your browser, then copy the final redirect URL.")

        async def callback():
            url = await asyncio.to_thread(getpass.getpass, "Redirect URL (hidden): ")
            values = parse_qs(urlsplit(url).query)
            return AuthorizationCodeResult(
                code=values.get("code", [""])[0],
                state=values.get("state", [None])[0],
                iss=values.get("iss", [None])[0],
            )

        provider = oauth_provider(
            args.principal,
            args.server,
            server,
            redirect_handler=redirect,
            callback_handler=callback,
            environ=harness.environ,
        )
        async with MCPAdapter(
            args.server, server, principal=args.principal, oauth=provider
        ) as adapter:
            print(
                "OAuth enrollment ready."
                if adapter.diagnostics["status"] == "ready"
                else "OAuth enrollment unavailable."
            )
            return 0 if adapter.diagnostics["status"] == "ready" else 1
    context = harness.context(principal=args.principal or f"cli:{uuid4()}", profile="cli")
    try:
        result = await harness.run(
            HarnessRequest(
                operation=args.operation,
                arguments=json.loads(args.arguments),
                profile="cli",
            ),
            context,
        )
        if args.text and result.ok:
            payload = result.data
            print(payload.get("response", payload) if isinstance(payload, dict) else payload)
        else:
            print(result.model_dump_json())
        return 0 if result.ok else 1
    finally:
        await factory.kw["bind"].dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", nargs="?", default="inspect")
    parser.add_argument("--arguments", default="{}", help="JSON capability arguments")
    parser.add_argument("--config", help="Versioned harness TOML file")
    parser.add_argument("--text", action="store_true")
    parser.add_argument("--server", help="Configured MCP server for OAuth setup")
    parser.add_argument("--principal", help="Explicit principal for OAuth enrollment/revocation")
    args = parser.parse_args()
    try:
        code = asyncio.run(run_cli(args))
    except (ValueError, OSError):
        parser.exit(2, "Invalid harness configuration or arguments.\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
