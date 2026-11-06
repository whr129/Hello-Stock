"""Operator commands with deliberately small, credential-free output."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select

from news_agent.alerts.collector import FastNewsCollector
from news_agent.alerts.config import resolve_alert_settings
from news_agent.alerts.delivery import MarketAlertDispatcher
from news_agent.alerts.service import MarketAlertService
from news_agent.alerts.storage import MarketAlertDecision, MarketAlertDelivery, MarketAlertEvent
from news_agent.settings import Settings, get_settings
from news_agent.storage.database import create_session_factory
from news_agent.storage.models import Source


async def check_credentials(settings: Settings) -> tuple[int, dict[str, Any]]:
    key = settings.openrouter_api_key.get_secret_value().strip()
    if not key:
        return 2, {"status": "missing", "explanation": "Set OPENROUTER_API_KEY."}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}
            )
        if response.status_code == 401:
            return 2, {
                "status": "unauthorized",
                "http_status": 401,
                "explanation": "Key rejected: invalid, revoked, or expired. Rotate it and "
                "restart the process; exported environment variables override .env.",
            }
        if response.status_code != 200:
            return 2, {
                "status": "unavailable",
                "http_status": response.status_code,
                "explanation": "Credential check failed; no model request was made.",
            }
        data = response.json().get("data", {})
        expires_at = data.get("expires_at")
        if expires_at:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= datetime.now(UTC):
                return 2, {
                    "status": "expired",
                    "explanation": "Rotate the expired key and "
                    "restart the process; environment variables override .env.",
                }
        return 0, {
            "status": "valid",
            "explanation": "OpenRouter accepted the key. "
            "No model request was made; Jev model access is not tested.",
        }
    except Exception:
        return 2, {
            "status": "unavailable",
            "explanation": "Could not safely verify the key; "
            "check connectivity and retry. No model request was made.",
        }


def confirmed_unauthorized(config: dict[str, Any]) -> bool:
    return config.get("quarantined_http_status") == 401 or (
        config.get("last_http_status") == 401
        and isinstance(config.get("unauthorized_failures"), int)
        and config["unauthorized_failures"] >= 2
    )


async def cleanup_unauthorized(factory: Any, *, write: bool) -> dict[str, Any]:
    async with factory() as session:
        query = select(Source)
        if write:
            query = query.with_for_update()
        sources = list((await session.execute(query)).scalars())
        matched = [source for source in sources if confirmed_unauthorized(source.config or {})]
        enabled = [source for source in matched if source.enabled]
        if write:
            for source in enabled:
                source.enabled = False
            await session.commit()
        return {
            "matched": len(matched),
            "would_disable": len(enabled),
            "disabled": len(enabled) if write else 0,
            "dry_run": not write,
        }


async def inspect_storage(factory: Any) -> dict[str, Any]:
    async with factory() as session:
        counts = {}
        for name, model in (
            ("events", MarketAlertEvent),
            ("decisions", MarketAlertDecision),
            ("deliveries", MarketAlertDelivery),
        ):
            counts[name] = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        statuses = {}
        for name, model in (
            ("decisions", MarketAlertDecision),
            ("deliveries", MarketAlertDelivery),
        ):
            statuses[name] = dict(
                (
                    await session.execute(select(model.status, func.count()).group_by(model.status))
                ).all()
            )
        recent = (
            await session.execute(
                select(
                    MarketAlertDelivery.id,
                    MarketAlertDelivery.status,
                    MarketAlertDelivery.created_at,
                )
                .order_by(MarketAlertDelivery.id.desc())
                .limit(10)
            )
        ).all()
        return {
            "counts": counts,
            "statuses": statuses,
            "recent_deliveries": [
                {"id": row.id, "status": row.status, "created_at": row.created_at.isoformat()}
                for row in recent
            ],
        }


async def run(args: argparse.Namespace, settings: Settings) -> tuple[int, dict[str, Any]]:
    if args.command == "check-credentials":
        return await check_credentials(settings)
    if args.command == "once":
        settings = resolve_alert_settings(settings)
    if args.command == "once" and not settings.market_alerts_enabled:
        return 2, {"status": "disabled", "explanation": "Set MARKET_ALERTS_ENABLED=true first."}
    factory = create_session_factory(settings)
    try:
        if args.command == "inspect":
            return 0, await inspect_storage(factory)
        if args.command == "cleanup-unauthorized":
            return 0, await cleanup_unauthorized(factory, write=args.write)
        service = MarketAlertService(factory, settings)
        try:
            settings = service.settings
            collected = await FastNewsCollector(factory, settings).collect_once()
            assessed = await service.process_once()
            delivered = await MarketAlertDispatcher(factory, settings).dispatch_once()
            return 0, {
                "collection": collected,
                "assessment": assessed,
                "delivered": delivered,
                "shadow_mode": settings.market_alert_shadow_mode,
            }
        finally:
            await service.aclose()
    finally:
        await factory.kw["bind"].dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Inspect and operate market alerts safely.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-credentials", help="Check the OpenRouter key without inference.")
    commands.add_parser("inspect", help="Show database counts and delivery statuses.")
    commands.add_parser("once", help="Collect, assess, and dispatch once using configured policy.")
    cleanup = commands.add_parser("cleanup-unauthorized", help="Disable confirmed 401 sources.")
    cleanup.add_argument("--write", action="store_true", help="Apply changes; default is dry-run.")
    args = parser.parse_args(argv)
    try:
        code, report = asyncio.run(run(args, get_settings()))
    except Exception:
        code, report = (
            2,
            {
                "status": "failed",
                "explanation": "Command failed. Check configuration, migration, and connectivity.",
            },
        )
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
