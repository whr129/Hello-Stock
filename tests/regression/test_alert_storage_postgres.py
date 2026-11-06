"""Opt-in destructive checks, restricted to the dedicated disposable alerts database."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from news_agent.alerts.storage import (
    AlertRepository,
    DeliveryRequest,
    MarketAlertBudget,
    MarketAlertDecision,
    MarketAlertDelivery,
    StaleClaimError,
)

URL = os.environ.get("NEWS_AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Dedicated PostgreSQL alerts test URL required")
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def safe_url():
    assert URL and make_url(URL).database == "news_agent_alert_tests"
    assert make_url(URL).host in {"127.0.0.1", "localhost"}
    return URL


def test_migration_round_trip():
    env = {**os.environ, "DATABASE_URL": safe_url(), "PYTHONPATH": "src"}
    for command in (
        ["upgrade", "head"],
        ["downgrade", "0006_market_research_only_cleanup"],
        ["upgrade", "head"],
    ):
        subprocess.run(
            [sys.executable, "-m", "alembic", *command], env=env, check=True, capture_output=True
        )
        if command[0] == "downgrade":
            asyncio.run(_historical_migration_article(insert=True))
    asyncio.run(_historical_migration_article(insert=False))


async def _historical_migration_article(*, insert):
    engine = create_async_engine(safe_url())
    try:
        async with engine.begin() as connection:
            url = "https://example.com/isolated-migration-backfill"
            if insert:
                await connection.execute(
                    text(
                        "INSERT INTO articles "
                        "(url,title,content_hash,category,related_tickers,created_at) "
                        "VALUES (:url,'Historical','old','markets','{}','2000-01-01T00:00:00Z')"
                    ),
                    {"url": url},
                )
            else:
                row = (
                    await connection.execute(
                        text("SELECT created_at,updated_at,revision FROM articles WHERE url=:url"),
                        {"url": url},
                    )
                ).one()
                assert row.created_at == row.updated_at
                assert row.updated_at.year == 2000
                assert row.revision == 1
                await connection.execute(text("DELETE FROM articles WHERE url=:url"), {"url": url})
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine(safe_url())
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE market_sentiment_snapshots, market_alert_deliveries, "
                "market_alert_decisions, market_alert_events, market_alert_budgets RESTART IDENTITY"
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def prepare(sessions, key="event", digest="input"):
    async with sessions.begin() as session:
        repo = AlertRepository(session)
        event = await repo.register_event(
            key,
            scope="company",
            event_type="earnings",
            tickers=["TEST"],
            evidence=[{"url": "https://example.com/evidence"}],
            payload={"fact": 1},
            revision_key="revision-1",
            now=NOW,
        )
        decision = await repo.prepare_decision(
            event.id,
            input_digest=digest,
            kind="news",
            model_revision="jev-test",
            input_payload={},
            now=NOW,
        )
        return event.id, decision.id


@pytest.mark.asyncio
async def test_parallel_event_and_decision_idempotency(sessions):
    results = await asyncio.gather(*(prepare(sessions) for _ in range(4)))
    assert len(set(results)) == 1
    decision_id = results[0][1]

    async def claim():
        async with sessions.begin() as session:
            item = await AlertRepository(session).claim_decision(
                decision_id, now=NOW, lease_seconds=120, max_attempts=3
            )
            return item.lease_token if item else None

    claims = await asyncio.gather(claim(), claim())
    assert sum(token is not None for token in claims) == 1


@pytest.mark.asyncio
async def test_parallel_default_source_bootstrap_is_idempotent(sessions):
    from uuid import uuid4

    from sqlalchemy import delete

    from news_agent.storage.models import Source
    from news_agent.storage.repositories import SourceRepository

    url = f"https://example.com/bootstrap-{uuid4().hex}"

    async def create():
        async with sessions.begin() as session:
            source = await SourceRepository(session).add_source(
                name="Isolated source",
                provider="rss",
                external_account=url,
                reactivate=False,
            )
            return source.id

    try:
        results = await asyncio.gather(*(create() for _ in range(4)))
        assert len(set(results)) == 1
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Source).where(Source.url == url))


@pytest.mark.asyncio
async def test_parallel_budget_reservation_and_settlement(sessions):
    ids = [(await prepare(sessions, key=f"event-{i}"))[1] for i in range(2)]

    async def reserve(decision_id):
        async with sessions.begin() as session:
            repo = AlertRepository(session)
            item = await repo.claim_decision(
                decision_id, now=NOW, lease_seconds=120, max_attempts=3
            )
            accepted = await repo.reserve_budget(
                item.id, item.lease_token, amount_usd="0.6", monthly_limit_usd="1", now=NOW
            )
            return item.id, item.lease_token, accepted

    results = await asyncio.gather(*(reserve(i) for i in ids))
    assert sum(result[2] for result in results) == 1
    winner, token, _ = next(result for result in results if result[2])
    async with sessions.begin() as session:
        await AlertRepository(session).complete_decision(
            winner,
            token,
            result={},
            delivery_choice="daily_report",
            actual_cost_usd="0.2",
            now=NOW,
        )
    async with sessions() as session:
        budget = await session.get(MarketAlertBudget, "2026-10")
        assert budget.spent_usd == Decimal("0.2")
        assert budget.reserved_usd == 0


async def queue(sessions):
    _, decision_id = await prepare(sessions)
    async with sessions.begin() as session:
        repo = AlertRepository(session)
        item = await repo.claim_decision(decision_id, now=NOW, lease_seconds=120, max_attempts=3)
        await repo.complete_decision(
            item.id,
            item.lease_token,
            result={},
            delivery_choice="immediate_alert",
            actual_cost_usd=0,
            now=NOW,
            delivery=DeliveryRequest(
                123, "news", "first second", NOW + timedelta(hours=1), chunks=("first", "second")
            ),
        )


@pytest.mark.asyncio
async def test_delivery_parallel_claim_retry_checkpoint_and_stale_worker(sessions):
    await queue(sessions)

    async def claim(now=NOW):
        async with sessions.begin() as session:
            item = await AlertRepository(session).claim_delivery(
                now=now, lease_seconds=120, max_attempts=3
            )
            return (item.id, item.lease_token) if item else None

    results = await asyncio.gather(claim(), claim())
    assert sum(result is not None for result in results) == 1
    delivery_id, token = next(result for result in results if result)
    async with sessions.begin() as session:
        repo = AlertRepository(session)
        await repo.checkpoint_chunk(delivery_id, token, 0, 100, now=NOW)
        await repo.finish_delivery(
            delivery_id, token, status="failed", now=NOW, retry_at=NOW + timedelta(seconds=1)
        )
    assert await claim() is None
    later = NOW + timedelta(seconds=2)
    _, next_token = await claim(later)
    async with sessions.begin() as session:
        with pytest.raises(StaleClaimError):
            await AlertRepository(session).checkpoint_chunk(delivery_id, token, 1, 101, now=later)
    async with sessions.begin() as session:
        repo = AlertRepository(session)
        item = await repo.checkpoint_chunk(delivery_id, next_token, 0, 100, now=later)
        assert item.chunks_sent == 1
        await repo.checkpoint_chunk(delivery_id, next_token, 1, 101, now=later)
        await repo.finish_delivery(delivery_id, next_token, status="delivered", now=later)
    assert await claim(later) is None
    async with sessions() as session:
        item = await session.get(MarketAlertDelivery, delivery_id)
        assert item.message_ids == [100, 101]
        assert item.attempts == 2


@pytest.mark.asyncio
async def test_crash_recovery_consumes_budget_and_never_replays_uncertain_delivery(sessions):
    await queue(sessions)
    _, pending_id = await prepare(sessions, key="assessment")
    async with sessions.begin() as session:
        repo = AlertRepository(session)
        decision = await repo.claim_decision(pending_id, now=NOW, lease_seconds=10, max_attempts=3)
        old_token = decision.lease_token
        await repo.reserve_budget(
            pending_id, old_token, amount_usd="0.025", monthly_limit_usd="5", now=NOW
        )
        delivery = await repo.claim_delivery(now=NOW, lease_seconds=10, max_attempts=3)
        delivery_id, delivery_token = delivery.id, delivery.lease_token
    later = NOW + timedelta(seconds=11)
    async with sessions.begin() as session:
        counts = await AlertRepository(session).recover_expired(now=later)
        assert counts == {"decisions": 1, "unknown": 1, "expired": 0}
    async with sessions.begin() as session:
        repo = AlertRepository(session)
        assert await repo.claim_delivery(now=later, lease_seconds=10, max_attempts=3) is None
        with pytest.raises(StaleClaimError):
            await repo.finish_delivery(delivery_id, delivery_token, status="delivered", now=later)
    async with sessions() as session:
        budget = await session.get(MarketAlertBudget, "2026-10")
        assert budget.spent_usd == Decimal("0.025")
        assert budget.reserved_usd == 0
        assert (await session.get(MarketAlertDelivery, delivery_id)).status == "unknown"
        decision = (
            await session.execute(
                select(MarketAlertDecision).where(MarketAlertDecision.id == pending_id)
            )
        ).scalar_one()
        assert decision.status == "failed"
        assert decision.reserved_usd == 0


@pytest.mark.asyncio
async def test_collectors_share_source_lease_and_preserve_article_metadata(sessions):
    import threading
    import time
    from uuid import uuid4

    from sqlalchemy import delete

    from news_agent.alerts.collector import FastNewsCollector
    from news_agent.ingestion.providers import NormalizedIngestItem
    from news_agent.settings import Settings
    from news_agent.storage.models import Article, Source

    identity = uuid4().hex
    url = f"https://example.com/{identity}"
    async with sessions.begin() as session:
        source = Source(
            name=identity,
            url=url,
            external_account=url,
            provider="rss",
            owner_user_id=None,
            category="markets",
            enabled=True,
            config={"alert_enabled": True},
            field_mapping={},
        )
        session.add(source)
        await session.flush()
        source_id = source.id
    calls = 0
    lock = threading.Lock()

    class Provider:
        def fetch_items(self, source, timeout_seconds):
            nonlocal calls
            if source.id != source_id:
                return []
            with lock:
                calls += 1
            time.sleep(0.05)
            return [
                NormalizedIngestItem(
                    identity,
                    url + "/news",
                    "TEST earnings",
                    "revenue",
                    None,
                    None,
                    {},
                    "rss",
                    url,
                    {"tickers": ["TEST"], "score": 0.75},
                )
            ]

    class Registry:
        def get(self, provider):
            return Provider()

    settings = Settings(_env_file=None, default_source_pack_enabled=False)
    try:
        collectors = [FastNewsCollector(sessions, settings, Registry()) for _ in range(2)]
        results = await asyncio.gather(*(collector.collect_once(NOW) for collector in collectors))
        assert calls == 1
        assert sum(result["saved"] for result in results) == 1
        async with sessions() as session:
            article = (
                await session.execute(select(Article).where(Article.url == url + "/news"))
            ).scalar_one()
            assert article.provider_metadata["score"] == 0.75
            assert article.related_tickers == ["TEST"]
            assert article.published_at is None
            source = await session.get(Source, source_id)
            assert source.last_fetched_at is None
            assert source.config["alert_last_fetched_at"] == NOW.isoformat()
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Article).where(Article.source_id == source_id))
            await session.execute(delete(Source).where(Source.id == source_id))


@pytest.mark.asyncio
async def test_same_url_corrections_are_atomic_and_keep_observation_time_separate(sessions):
    from uuid import uuid4

    from sqlalchemy import delete

    from news_agent.storage.models import (
        Article,
        ArticleEmbedding,
        MarketMention,
        Summary,
        SummaryEmbedding,
    )
    from news_agent.storage.repositories import (
        ArticleRepository,
        EmbeddingRepository,
        MarketMentionRepository,
        SummaryRepository,
    )

    url = f"https://example.com/correction/{uuid4().hex}"
    initial = dict(
        source_id=None,
        url=url,
        title="Earnings were 10",
        content_hash="old",
        category="markets",
        extracted_text="Revenue was 10",
        published_at=NOW,
        provider_metadata={"sentiment": 0.1},
    )
    async with sessions.begin() as session:
        article, changed = await ArticleRepository(session).upsert_article(**initial)
        article_id, created_at = article.id, article.created_at
        assert changed and article.revision == 1
        summary = Summary(article_id=article_id, text="Old evidence", model_name="test")
        session.add(summary)
        await session.flush()
        summary_id = summary.id
        session.add(
            MarketMention(
                article_id=article_id,
                ticker="TEST",
                theme="earnings",
                article_revision=1,
                evidence_text="Old evidence",
            )
        )
        session.add(
            ArticleEmbedding(article_id=article_id, embedding=[0.0] * 1536, embedding_model="test")
        )
        session.add(
            SummaryEmbedding(summary_id=summary_id, embedding=[0.0] * 1536, embedding_model="test")
        )
    try:
        async with sessions.begin() as session:
            _, changed = await ArticleRepository(session).upsert_article(**initial)
            assert not changed
        correction = {
            **initial,
            "title": "Correction: earnings were 20",
            "content_hash": "new",
            "extracted_text": "Revenue was 20",
            "published_at": None,
        }

        async def update():
            async with sessions.begin() as session:
                item, changed = await ArticleRepository(session).upsert_article(**correction)
                return item.revision, changed

        results = await asyncio.gather(update(), update())
        assert sorted(results) == [(2, False), (2, True)]
        async with sessions.begin() as session:
            item = await session.get(Article, article_id)
            assert item.created_at == created_at
            assert item.published_at == NOW
            assert item.title == correction["title"]
            assert "updated_at" not in item.provider_metadata
            observed_at = item.updated_at
            assert article_id in [
                a.id for a in await ArticleRepository(session).list_without_summaries()
            ]
            mentions = MarketMentionRepository(session)
            assert article_id in [a.id for a, _ in await mentions.list_articles_for_extraction()]
            assert summary_id not in [
                s.id for s, _, _ in await mentions.list_summaries_for_extraction()
            ]
            assert not await mentions.aggregate(since=NOW - timedelta(days=3650))
            revised_summary = await SummaryRepository(session).save_article_summary(
                article_id=article_id, text="Corrected evidence", model_name="test"
            )
            assert revised_summary.article_revision == 2
            await mentions.save(
                MarketMention(
                    article_id=article_id,
                    ticker="TEST",
                    theme="earnings",
                    article_revision=2,
                    evidence_text="Corrected evidence",
                )
            )
            aggregates = await mentions.aggregate(since=NOW - timedelta(days=3650))
            assert len(aggregates) == 1 and aggregates[0].mention_count == 1
            assert article_id not in [
                a.id for a in await ArticleRepository(session).list_without_summaries()
            ]
            assert article_id not in [
                a.id for a, _ in await mentions.list_articles_for_extraction()
            ]
            assert await session.get(Summary, summary_id) is not None
            assert (
                await session.execute(
                    select(ArticleEmbedding).where(ArticleEmbedding.article_id == article_id)
                )
            ).scalar_one_or_none() is None
            assert (
                await session.execute(
                    select(SummaryEmbedding).where(SummaryEmbedding.summary_id == summary_id)
                )
            ).scalar_one_or_none() is None
            embedding_repo = EmbeddingRepository(session)
            assert (
                await embedding_repo.save_article_embedding(
                    article_id, [0.0] * 1536, "stale", expected_article_revision=1
                )
                is None
            )
            assert (
                await embedding_repo.save_summary_embedding(
                    summary_id, [0.0] * 1536, "stale", expected_article_revision=1
                )
                is None
            )
            current_embedding = await embedding_repo.save_article_embedding(
                article_id, [0.0] * 1536, "current", expected_article_revision=2
            )
            assert current_embedding.chunk_metadata["article_revision"] == 2
            item, changed = await ArticleRepository(session).upsert_article(
                **{**correction, "provider_metadata": {"sentiment": 0.8}}
            )
            assert changed and item.revision == 2
            assert item.updated_at == observed_at
            assert item.provider_metadata["sentiment"] == 0.8
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(MarketMention).where(MarketMention.article_id == article_id)
            )
            await session.execute(delete(Summary).where(Summary.article_id == article_id))
            await session.execute(delete(Article).where(Article.id == article_id))
