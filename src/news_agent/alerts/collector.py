"""Fast, leased news collection. Assessment and enrichment run separately."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from news_agent.graph.nodes import (
    _default_sources_from_settings,
    _exclude_items_for_source,
    _limit_articles_for_source,
    _related_tickers_for_title,
    _source_in_pipeline,
)
from news_agent.ingestion.dedupe import content_hash
from news_agent.ingestion.market_impact import MarketImpactClassifier
from news_agent.ingestion.providers import IngestProviderRegistry
from news_agent.settings import Settings
from news_agent.storage.models import Source
from news_agent.storage.repositories import ArticleRepository, SourceRepository


def _timestamp(value: object) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
    except (ValueError, TypeError):
        return None


def _seconds(config: dict[str, Any], key: str, fallback: int) -> int:
    try:
        return max(1, int(config.get(key, fallback)))
    except (ValueError, TypeError):
        return fallback


class FastNewsCollector:
    def __init__(
        self,
        factory: Callable[[], AsyncSession],
        settings: Settings,
        registry: IngestProviderRegistry | None = None,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.registry = registry or IngestProviderRegistry(settings)
        self.classifier = MarketImpactClassifier(settings)

    async def collect_once(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        counts = dict.fromkeys(
            ("sources", "fetched", "saved", "duplicates", "rejected", "failed", "quarantined"), 0
        )
        async with self.factory() as session:
            defaults = _default_sources_from_settings(self.settings)
            if defaults:
                await SourceRepository(session).ensure_default_sources(defaults)
                await session.commit()
            ids = list(
                (
                    await session.execute(
                        select(Source.id).where(
                            Source.enabled.is_(True), Source.owner_user_id.is_(None)
                        )
                    )
                ).scalars()
            )
        semaphore = asyncio.Semaphore(self.settings.market_alert_concurrency)

        async def run(source_id):
            async with semaphore:
                try:
                    result = await self._collect_source(source_id, now)
                except Exception:
                    # Never include provider exception strings (URLs may contain credentials).
                    result = {"failed": 1}
                for key, value in result.items():
                    counts[key] += value

        await asyncio.gather(*(run(source_id) for source_id in ids))
        return counts

    async def _claim(self, source_id: int, now: datetime) -> tuple[Source, str] | None:
        async with self.factory() as session:
            source = (
                await session.execute(
                    select(Source).where(Source.id == source_id).with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if source is None or not source.enabled or source.owner_user_id is not None:
                return None
            config = dict(source.config or {})
            if config.get("alert_enabled") is False or config.get("quarantined_http_status") == 401:
                return None
            if config.get("alert_enabled") is not True and not _source_in_pipeline(
                source, "breaking_resources"
            ):
                return None
            interval = _seconds(
                config, "alert_poll_seconds", self.settings.market_alert_poll_seconds
            )
            interval = max(interval, _seconds(config, "provider_min_interval_seconds", 1))
            if source.provider in {"alpha_vantage", "finnhub", "polygon"}:
                interval = max(
                    interval,
                    _seconds(
                        config,
                        "fetch_interval_seconds",
                        self.settings.source_default_fetch_interval_seconds,
                    ),
                )
            last = _timestamp(config.get("alert_last_fetched_at"))
            lease = _timestamp(config.get("alert_lease_until"))
            if (last and (now - last).total_seconds() < interval) or (lease and lease > now):
                return None
            token = str(uuid4())
            config["alert_lease_token"] = token
            config["alert_lease_until"] = (
                now
                + timedelta(
                    seconds=max(
                        self.settings.market_alert_lease_seconds,
                        self.settings.rss_fetch_timeout_seconds + 30,
                    )
                )
            ).isoformat()
            config["alert_last_fetched_at"] = now.isoformat()
            source.config = config
            # Detach a complete provider snapshot before commit/session expiry.
            snapshot = Source(
                id=source.id,
                name=source.name,
                url=source.url,
                provider=source.provider,
                external_account=source.external_account,
                config=dict(config),
                field_mapping=dict(source.field_mapping or {}),
                category=source.category,
                fetch_mode=source.fetch_mode,
            )
            await session.commit()
            return snapshot, token

    async def _collect_source(self, source_id: int, now: datetime) -> dict[str, int]:
        claim = await self._claim(source_id, now)
        if claim is None:
            return {}
        source, token = claim
        counts = {
            "sources": 1,
            "fetched": 0,
            "saved": 0,
            "duplicates": 0,
            "rejected": 0,
            "failed": 0,
            "quarantined": 0,
        }
        error = None
        status = None
        items = []
        try:
            provider = self.registry.get(source.provider)
            items = await asyncio.to_thread(
                provider.fetch_items, source, self.settings.rss_fetch_timeout_seconds
            )
        except HTTPError as exc:
            status, error = exc.code, f"http_{exc.code}"
        except Exception:
            error = "provider_failed"
        async with self.factory() as session:
            current = (
                await session.execute(
                    select(Source).where(Source.id == source_id).with_for_update()
                )
            ).scalar_one_or_none()
            if current is None or (current.config or {}).get("alert_lease_token") != token:
                return counts
            config = dict(current.config or {})
            config.pop("alert_lease_token", None)
            config.pop("alert_lease_until", None)
            config["alert_last_error"] = error
            if error:
                counts["failed"] = 1
                if status == 401:
                    config["unauthorized_failures"] = (
                        int(config.get("unauthorized_failures", 0)) + 1
                    )
                    if config["unauthorized_failures"] >= 2:
                        current.enabled = False
                        config["quarantined_http_status"] = 401
                        counts["quarantined"] = 1
                else:
                    config.pop("unauthorized_failures", None)
            else:
                config.pop("unauthorized_failures", None)
                config["alert_last_success_at"] = now.isoformat()
                source_dict = {"config": source.config}
                items = _exclude_items_for_source(source_dict, items, self.settings)
                items = _limit_articles_for_source(source_dict, items, self.settings)
                counts["fetched"] = len(items)
                repo = ArticleRepository(session)
                universe = {
                    symbol.strip().upper()
                    for symbol in self.settings.market_universe_symbols.split(",")
                    if symbol.strip()
                }
                for item in items:
                    classification = self.classifier.classify_deterministic(
                        title=item.title, text=item.body_text or "", category=source.category
                    )
                    if not classification.accepted:
                        counts["rejected"] += 1
                        continue
                    metadata = dict(item.metadata or {})
                    metadata["market_impact"] = classification.metadata()
                    tickers = set(_related_tickers_for_title(item.title, universe))
                    raw_tickers = metadata.get("tickers", [])
                    if isinstance(raw_tickers, list):
                        tickers.update(t.upper() for t in raw_tickers if isinstance(t, str))
                    sentiment_rows = metadata.get("ticker_sentiment")
                    if isinstance(sentiment_rows, list):
                        tickers.update(
                            row["ticker"].upper()
                            for row in sentiment_rows
                            if isinstance(row, dict) and isinstance(row.get("ticker"), str)
                        )
                    _, changed = await repo.upsert_article(
                        source_id=source.id,
                        url=item.url,
                        title=item.title,
                        published_at=item.published_at,
                        author=item.author,
                        extracted_text=item.body_text,
                        category=source.category,
                        content_hash=content_hash(item.title, item.body_text, item.url),
                        related_tickers=sorted(tickers),
                        provider_metadata=metadata,
                    )
                    # Saved includes same-URL corrections and provider metadata updates.
                    counts["saved" if changed else "duplicates"] += 1
            current.config = config
            await session.commit()
        return counts
