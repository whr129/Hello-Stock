from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from news_agent.graph.nodes import (
    SchedulerNodes,
    _default_sources_from_settings,
    _related_tickers_for_title,
    _source_is_due,
)
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
    assert result["metadata"]["rejected_article_count"] == 1
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


def test_default_sources_are_empty_unless_configured() -> None:
    settings = Settings(openai_api_key="")

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
