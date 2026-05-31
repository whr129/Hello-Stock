from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from news_agent.graph.nodes import (
    SchedulerNodes,
    _default_sources_from_settings,
    _exclude_items_for_source,
    _parse_symbol_csv,
    _related_tickers_for_title,
    _source_in_pipeline,
    _source_is_due,
)
from news_agent.ingestion.providers import NormalizedIngestItem
from news_agent.settings import Settings


@pytest.mark.asyncio
async def test_normalize_dedupe_saves_only_market_impact_articles(monkeypatch) -> None:
    upserted_titles: list[str] = []

    class FakeArticleRepository:
        def __init__(self, session) -> None:
            del session

        async def upsert_article(self, **kwargs):
            upserted_titles.append(kwargs["title"])
            return (
                SimpleNamespace(
                    id=len(upserted_titles),
                    title=kwargs["title"],
                    extracted_text=kwargs.get("extracted_text"),
                ),
                True,
            )

    class FakeMarketRepository:
        def __init__(self, session) -> None:
            del session

        async def save_snapshot(self, **kwargs):
            del kwargs

    monkeypatch.setattr("news_agent.graph.nodes.ArticleRepository", FakeArticleRepository)
    monkeypatch.setattr("news_agent.graph.nodes.MarketRepository", FakeMarketRepository)

    node = SchedulerNodes.__new__(SchedulerNodes)
    node.session_factory = lambda: _FakeSessionContext()
    node.market_impact_classifier = _FakeClassifier(
        {
            "Earnings beat sends shares higher": True,
            "Local sports tournament starts": False,
        }
    )

    result = await node.normalize_dedupe(
        {
            "due_tickers": ["AAPL"],
            "fetched_articles": [
                {
                    "source_id": 1,
                    "source_name": "Example",
                    "provider": "rss",
                    "category": "general",
                    "title": "Earnings beat sends shares higher",
                    "url": "https://example.com/earnings",
                    "published_at": None,
                    "summary": "Revenue and guidance improved.",
                    "author": "Reporter",
                },
                {
                    "source_id": 1,
                    "source_name": "Example",
                    "provider": "rss",
                    "category": "general",
                    "title": "Local sports tournament starts",
                    "url": "https://example.com/sports",
                    "published_at": None,
                    "summary": "A community event.",
                    "author": "Reporter",
                },
            ],
            "market_snapshots": [],
            "metadata": {},
        }
    )

    assert upserted_titles == ["Earnings beat sends shares higher"]
    assert result["metadata"]["saved_article_count"] == 1
    assert result["metadata"]["accepted_article_count"] == 1
    assert result["metadata"]["rejected_article_count"] == 1
    assert result["metadata"]["duplicate_article_count"] == 0
    assert len(result["metadata"]["market_impact_classifications"]) == 2


def test_related_tickers_for_title_does_not_match_substrings_or_bare_one_letter_words() -> None:
    assert _related_tickers_for_title(
        "I inherited a house for the appraised value",
        {"A", "V"},
    ) == []


def test_related_tickers_for_title_matches_explicit_symbols() -> None:
    assert _related_tickers_for_title(
        "$V rises as AAPL reports earnings",
        {"AAPL", "V"},
    ) == ["AAPL", "V"]


def test_source_due_logic_respects_configured_interval() -> None:
    settings = Settings(openai_api_key="", source_default_fetch_interval_seconds=900)
    source = SimpleNamespace(
        last_fetched_at=datetime.now(UTC) - timedelta(seconds=300),
        config={"fetch_interval_seconds": 600},
    )

    assert _source_is_due(source, settings) is False


def test_source_due_logic_fetches_stale_source() -> None:
    settings = Settings(openai_api_key="", source_default_fetch_interval_seconds=900)
    source = SimpleNamespace(
        last_fetched_at=datetime.now(UTC) - timedelta(seconds=901),
        config={},
    )

    assert _source_is_due(source, settings) is True


def test_parse_symbol_csv_accepts_yahoo_class_tickers() -> None:
    assert _parse_symbol_csv("AAPL,BRK-B,BRK.B,INVALID!") == ["AAPL", "BRK-B", "BRK.B"]


def test_source_pipeline_filter_uses_configured_tier() -> None:
    breaking = SimpleNamespace(config={"pipeline_tier": "breaking_resources"})
    daily = SimpleNamespace(config={"pipeline_tier": "daily_resources"})
    legacy = SimpleNamespace(config={})

    assert _source_in_pipeline(breaking, "breaking_resources") is True
    assert _source_in_pipeline(daily, "breaking_resources") is False
    assert _source_in_pipeline(daily, "daily_resources") is True
    assert _source_in_pipeline(legacy, "breaking_resources") is True


def test_exclude_items_for_source_drops_reuters_metadata() -> None:
    settings = Settings(openai_api_key="")
    articles = [
        SimpleNamespace(
            title="Semiconductor demand improves",
            author="Reuters",
            account="market-news",
            provider="finnhub",
            metadata={"provider_source": "Reuters"},
        ),
        SimpleNamespace(
            title="Cloud capex rises",
            author="Company Wire",
            account="market-news",
            provider="rss",
            metadata={"provider_source": "Company Wire"},
        ),
    ]

    filtered = _exclude_items_for_source({"config": {}}, articles, settings)

    assert [article.title for article in filtered] == ["Cloud capex rises"]


def test_default_sources_can_be_disabled() -> None:
    settings = Settings(openai_api_key="", default_source_pack_enabled=False)

    assert _default_sources_from_settings(settings) == []


def test_default_sources_load_from_json_config() -> None:
    settings = Settings(
        openai_api_key="",
        default_sources_json=(
            '[{"name":"SEC 8-K","provider":"rss",'
            '"feed_url":"https://www.sec.gov/news/pressreleases.rss",'
            '"category":"filings"}]'
        ),
    )

    assert _default_sources_from_settings(settings) == [
        {
            "name": "SEC 8-K",
            "provider": "rss",
            "feed_url": "https://www.sec.gov/news/pressreleases.rss",
            "category": "filings",
        }
    ]


def test_default_sources_skip_optional_api_sources_without_keys() -> None:
    settings = Settings(
        openai_api_key="",
        default_sources_json=(
            '[{"name":"Alpha","provider":"alpha_vantage","external_account":"NEWS_SENTIMENT",'
            '"category":"market_news","config":{"max_items":10}},'
            '{"name":"RSS","provider":"rss","feed_url":"https://example.com/rss",'
            '"category":"market_news","config":{"max_items":10}}]'
        ),
    )

    assert [source["name"] for source in _default_sources_from_settings(settings)] == ["RSS"]


@pytest.mark.asyncio
async def test_fetch_parallel_retries_source_and_records_metadata(monkeypatch) -> None:
    calls = {"source": 0}
    finished_steps: list[dict[str, object]] = []
    fetch_results: list[dict[str, object]] = []

    class FakeProvider:
        def fetch_items(self, source, timeout_seconds: int):
            del source, timeout_seconds
            calls["source"] += 1
            if calls["source"] == 1:
                raise TimeoutError("temporary source failure")
            return [
                NormalizedIngestItem(
                    external_id="1",
                    url="https://example.com/a",
                    title="AAPL raises guidance",
                    body_text="Revenue improved.",
                    published_at=None,
                    author=None,
                    raw_payload={},
                    provider="rss",
                    account="example",
                    metadata={},
                )
            ]

    class FakeRegistry:
        def get(self, provider: str):
            assert provider == "rss"
            return FakeProvider()

    class FakeTraceService:
        async def start_step(self, **kwargs):
            del kwargs
            return 10

        async def finish_step(self, step_id, **kwargs):
            finished_steps.append({"step_id": step_id, **kwargs})

        async def record_error(self, **kwargs):
            del kwargs
            return 1

    class FakeSourceRepository:
        def __init__(self, session) -> None:
            del session

        async def mark_fetch_result(self, *args, **kwargs):
            fetch_results.append({"args": args, **kwargs})

    monkeypatch.setattr("news_agent.graph.nodes.SourceRepository", FakeSourceRepository)

    node = SchedulerNodes.__new__(SchedulerNodes)
    node.settings = Settings(
        openai_api_key="",
        source_fetch_max_attempts=2,
        source_fetch_retry_backoff_seconds=0,
        market_fetch_max_attempts=1,
    )
    node.ingest_registry = FakeRegistry()
    node.trace_service = FakeTraceService()
    node.session_factory = lambda: _FakeSessionContext()

    result = await node.fetch_parallel(
        {
            "runtime_run_id": 99,
            "active_step_id": 1,
            "job_type": "market_research_refresh",
            "due_sources": [
                {
                    "id": 1,
                    "name": "Example",
                    "url": "https://example.com/feed.xml",
                    "provider": "rss",
                    "external_account": "https://example.com/feed.xml",
                    "config": {},
                    "field_mapping": {},
                    "fetch_mode": "rss",
                    "enabled": True,
                    "trust_score": 0.5,
                    "last_fetched_at": None,
                    "last_success_at": None,
                    "last_error": None,
                    "category": "markets",
                }
            ],
            "due_tickers": [],
            "errors": [],
            "metadata": {"fetch_metrics": {"sources": {"due": 1}, "tickers": {}, "retry_count": 0}},
        }
    )

    metrics = result["metadata"]["fetch_metrics"]
    assert calls["source"] == 2
    assert metrics["retry_count"] == 1
    assert metrics["sources"]["succeeded"] == 1
    assert metrics["sources"]["items_fetched"] == 1
    assert result["metadata"]["provider_counts"] == {"rss": 1}
    assert fetch_results[0]["success"] is True
    assert finished_steps[0]["metadata"]["retry_count"] == 1
    assert [attempt["status"] for attempt in finished_steps[0]["metadata"]["attempts"]] == [
        "failed",
        "completed",
    ]


class _FakeClassifier:
    def __init__(self, decisions: dict[str, bool]) -> None:
        self.decisions = decisions

    async def classify(self, **kwargs):
        title = kwargs["title"]
        return _FakeDecision(self.decisions[title])


class _FakeDecision:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted

    def metadata(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "confidence": 0.9,
            "reason": "test",
            "method": "test",
        }


class _FakeSession:
    async def commit(self) -> None:
        pass


class _FakeSessionContext:
    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False
