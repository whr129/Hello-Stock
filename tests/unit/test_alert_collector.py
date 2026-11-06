import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.error import HTTPError

import pytest
from sqlalchemy.dialects import postgresql

from news_agent.alerts.collector import FastNewsCollector
from news_agent.ingestion.providers import NormalizedIngestItem
from news_agent.settings import Settings
from news_agent.storage.models import Source
from news_agent.storage.repositories import ArticleRepository

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def source(**overrides):
    return Source(
        **(
            {
                "id": 1,
                "name": "test",
                "url": "https://example.com/feed",
                "external_account": "https://example.com/feed",
                "provider": "rss",
                "category": "markets",
                "enabled": True,
                "owner_user_id": None,
                "field_mapping": {},
                "config": {"pipeline_tier": "breaking"},
            }
            | overrides
        )
    )


class Session:
    def __init__(self, item=None, ids=()):
        self.execute = AsyncMock(
            return_value=Mock(
                scalar_one_or_none=Mock(return_value=item), scalars=Mock(return_value=ids)
            )
        )
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def collector(session, registry=None):
    return FastNewsCollector(
        lambda: session,
        Settings(_env_file=None, default_source_pack_enabled=False, market_alert_concurrency=2),
        registry,
    )


@pytest.mark.asyncio
async def test_claim_uses_separate_clock_and_commits_before_fetch():
    item = source(last_fetched_at=NOW)
    session = Session(item)
    instance = collector(session)
    claimed = await instance._claim(1, NOW)
    assert claimed is not None
    session.commit.assert_awaited_once()
    assert item.last_fetched_at == NOW
    assert item.config["alert_last_fetched_at"] == NOW.isoformat()
    assert "SKIP LOCKED" in str(
        session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    )
    assert await instance._claim(1, NOW) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"owner_user_id": 3},
        {"enabled": False},
        {"config": {"pipeline_tier": "daily"}},
        {"config": {"alert_enabled": False}},
        {"config": {"quarantined_http_status": 401}},
        {
            "provider": "alpha_vantage",
            "config": {
                "alert_poll_seconds": 10,
                "fetch_interval_seconds": 600,
                "alert_last_fetched_at": (NOW - timedelta(seconds=60)).isoformat(),
            },
        },
    ],
)
async def test_claim_respects_scope_and_quota(overrides):
    assert await collector(Session(source(**overrides)))._claim(1, NOW) is None


@pytest.mark.asyncio
async def test_bounded_overlap_and_failure_does_not_discard_others():
    instance = collector(Session(ids=range(6)))
    active = peak = 0

    async def collect(source_id, now):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        if source_id == 0:
            raise RuntimeError("secret must never appear in counts")
        return {"saved": 1}

    instance._collect_source = collect
    counts = await instance.collect_once(NOW)
    assert peak == 2
    assert counts["failed"] == 1
    assert counts["saved"] == 5
    assert all(value >= 0 for value in counts.values())


@pytest.mark.asyncio
async def test_metadata_and_missing_timestamp_preserved(monkeypatch):
    item = source(config={"alert_lease_token": "token"})
    ingest = NormalizedIngestItem(
        "id",
        "https://example.com/news",
        "TEST earnings",
        "Revenue improved",
        None,
        "wire",
        {},
        "rss",
        "account",
        {"tickers": ["TEST"], "sentiment": 0.7},
    )
    registry = Mock(get=Mock(return_value=Mock(fetch_items=Mock(return_value=[ingest]))))
    instance = collector(Session(item), registry)
    instance._claim = AsyncMock(return_value=(item, "token"))
    save = AsyncMock(return_value=(SimpleNamespace(id=1), True))
    monkeypatch.setattr(ArticleRepository, "upsert_article", save)
    counts = await instance._collect_source(1, NOW)
    assert counts["saved"] == 1
    assert save.call_args.kwargs["published_at"] is None
    assert save.call_args.kwargs["provider_metadata"]["sentiment"] == 0.7
    assert save.call_args.kwargs["related_tickers"] == ["TEST"]
    assert item.last_fetched_at is None


@pytest.mark.asyncio
async def test_two_typed_401s_quarantine_without_bulk_clock():
    item = source(config={"alert_lease_token": "token"})
    error = HTTPError("https://example.com", 401, "secret", {}, None)
    registry = Mock(get=Mock(return_value=Mock(fetch_items=Mock(side_effect=error))))
    instance = collector(Session(item), registry)
    instance._claim = AsyncMock(return_value=(item, "token"))
    first = await instance._collect_source(1, NOW)
    assert first["quarantined"] == 0
    item.config = {**item.config, "alert_lease_token": "token"}
    second = await instance._collect_source(1, NOW + timedelta(minutes=1))
    assert second["quarantined"] == 1
    assert not item.enabled
    assert item.config["alert_last_error"] == "http_401"
    assert item.last_fetched_at is None


@pytest.mark.asyncio
async def test_article_insert_is_atomic_url_conflict():
    article = SimpleNamespace(
        id=2,
        title="title",
        content_hash="hash",
        extracted_text=None,
        provider_metadata={"sentiment": 0.5},
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                Mock(scalar_one_or_none=Mock(return_value=None)),
                Mock(scalar_one=Mock(return_value=article)),
            ]
        )
    )
    result, created = await ArticleRepository(session).upsert_article(
        source_id=1,
        url="https://example.com",
        title="title",
        content_hash="hash",
        category="markets",
        provider_metadata={"sentiment": 0.5},
        related_tickers=["test"],
    )
    assert result is article and not created
    query = session.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect())
    assert "ON CONFLICT (url) DO NOTHING" in str(query)
    assert query.params["provider_metadata"] == {"sentiment": 0.5}


@pytest.mark.asyncio
async def test_embedding_stage_drains_fast_collector_backlog(monkeypatch):
    from news_agent.graph.nodes import SchedulerNodes

    item = SimpleNamespace(id=9, title="market earnings", extracted_text="revenue", revision=2)
    missing = AsyncMock(return_value=[item])
    saved = AsyncMock()
    monkeypatch.setattr(ArticleRepository, "list_without_embeddings", missing)
    monkeypatch.setattr("news_agent.graph.nodes.EmbeddingRepository.save_article_embedding", saved)
    node = SchedulerNodes.__new__(SchedulerNodes)
    node.settings = Settings(_env_file=None)
    session = Session()
    session.get = AsyncMock(return_value=item)
    node.session_factory = lambda: session
    node.embedding_service = SimpleNamespace(embed_text=AsyncMock(return_value=[0.1]))
    state = {"saved_articles": [], "job_type": "breaking_resources"}
    assert await node.embed_store(state) is state
    missing.assert_awaited_once_with(limit=20)
    assert saved.call_args.kwargs["article_id"] == 9
    assert saved.call_args.kwargs["expected_article_revision"] == 2
    node.embedding_service.embed_text.assert_awaited_once_with("market earnings\nrevenue")


@pytest.mark.asyncio
async def test_summary_generated_before_correction_is_not_persisted(monkeypatch):
    from news_agent.graph.nodes import SchedulerNodes
    from news_agent.storage.repositories import SummaryRepository

    article = SimpleNamespace(
        id=9, title="old earnings", extracted_text="old facts", revision=1, source_id=1
    )
    monkeypatch.setattr(
        ArticleRepository, "list_without_summaries", AsyncMock(return_value=[article])
    )
    save = AsyncMock()
    monkeypatch.setattr(SummaryRepository, "save_article_summary", save)
    session = Session()
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=2))
    node = SchedulerNodes.__new__(SchedulerNodes)
    node.settings = Settings(_env_file=None)
    node.session_factory = lambda: session
    node.summarizer = SimpleNamespace(summarize_article=AsyncMock(return_value="old summary"))
    node.embedding_service = SimpleNamespace(embed_text=AsyncMock(return_value=[0.1]))
    result = await node.precompute_summaries({"job_type": "breaking_resources"})
    assert result["summaries"] == []
    save.assert_not_awaited()
