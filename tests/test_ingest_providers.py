from types import SimpleNamespace

import pytest

from news_agent.ingestion.providers import (
    AccountFeedProvider,
    AlphaVantageNewsProvider,
    FinnhubNewsProvider,
    PolygonNewsProvider,
    RSSIngestProvider,
)


class FakeArticle:
    def __init__(self, title: str, url: str) -> None:
        self.title = title
        self.url = url
        self.summary = "Summary"
        self.published_at = None
        self.author = "Author"
        self.raw_payload = {"headline": title, "body": "Summary", "link": url, "writer": "Author"}


def test_rss_provider_maps_feed_items(monkeypatch) -> None:
    monkeypatch.setattr(
        "news_agent.ingestion.providers.parse_feed",
        lambda url, timeout_seconds: [FakeArticle("Example", "https://example.com")],
    )
    source = SimpleNamespace(
        id=1,
        provider="rss",
        external_account="https://feed.example/rss",
        url="https://feed.example/rss",
        config={"feed_url": "https://feed.example/rss"},
        field_mapping={},
    )

    items = RSSIngestProvider().fetch_items(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].title == "Example"
    assert items[0].provider == "rss"


@pytest.mark.parametrize(
    "feed_url",
    [
        "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "https://www.sec.gov/cgi-bin/browse-edgar?"
        "action=getcurrent&company=&type=8-K&dateb=&owner=include&start=0&count=40"
        "&output=atom",
        "https://www.eia.gov/rss/todayinenergy.xml",
    ],
)
def test_rss_provider_smoke_maps_official_feed_shapes(monkeypatch, feed_url: str) -> None:
    calls: list[tuple[str, int]] = []

    def fake_parse_feed(url: str, timeout_seconds: int) -> list[FakeArticle]:
        calls.append((url, timeout_seconds))
        return [FakeArticle("Official update", "https://example.gov/update")]

    monkeypatch.setattr("news_agent.ingestion.providers.parse_feed", fake_parse_feed)
    source = SimpleNamespace(
        id=1,
        provider="rss",
        external_account=feed_url,
        url=feed_url,
        config={"feed_url": feed_url},
        field_mapping={},
    )

    items = RSSIngestProvider().fetch_items(source, timeout_seconds=7)

    assert calls == [(feed_url, 7)]
    assert len(items) == 1
    assert items[0].external_id == "https://example.gov/update"
    assert items[0].account == feed_url


def test_account_feed_provider_uses_field_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        "news_agent.ingestion.providers.parse_feed",
        lambda url, timeout_seconds: [FakeArticle("Ignored", "https://example.com/post")],
    )
    source = SimpleNamespace(
        id=2,
        provider="twitter",
        external_account="@openai",
        url="twitter://@openai",
        config={"feed_url": "https://feed.example/twitter/openai"},
        field_mapping={
            "title_field": "headline",
            "body_field": "body",
            "url_field": "link",
            "author_field": "writer",
        },
    )

    items = AccountFeedProvider("twitter").fetch_items(source, timeout_seconds=5)

    assert items[0].title == "Ignored"
    assert items[0].body_text == "Summary"
    assert items[0].author == "Author"


def test_twitter_provider_requires_feed_url() -> None:
    source = SimpleNamespace(
        id=2,
        provider="twitter",
        external_account="@openai",
        url="twitter://@openai",
        config={},
        field_mapping={},
    )

    with pytest.raises(ValueError, match="twitter source requires config.feed_url"):
        AccountFeedProvider("twitter").fetch_items(source, timeout_seconds=5)


def test_alpha_vantage_provider_maps_news_sentiment(monkeypatch) -> None:
    monkeypatch.setattr(
        "news_agent.ingestion.providers._fetch_json",
        lambda url, timeout_seconds: {
            "feed": [
                {
                    "title": "NVDA expands AI capacity",
                    "url": "https://example.com/nvda",
                    "summary": "Semiconductor demand improved.",
                    "time_published": "20260529T143000",
                    "source": "Example Wire",
                }
            ]
        },
    )
    source = SimpleNamespace(
        id=3,
        provider="alpha_vantage",
        external_account="NEWS_SENTIMENT",
        config={"api_key": "test", "tickers": ["NVDA"], "topics": ["technology"]},
    )

    items = AlphaVantageNewsProvider().fetch_items(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].title == "NVDA expands AI capacity"
    assert items[0].provider == "alpha_vantage"


def test_finnhub_provider_maps_market_news(monkeypatch) -> None:
    monkeypatch.setattr(
        "news_agent.ingestion.providers._fetch_json",
        lambda url, timeout_seconds: [
            {
                "id": 42,
                "headline": "Fed decision moves markets",
                "url": "https://example.com/fed",
                "summary": "Treasury yields moved.",
                "datetime": 1780055400,
                "source": "Example Wire",
            }
        ],
    )
    source = SimpleNamespace(
        id=4,
        provider="finnhub",
        external_account="market-news",
        config={"api_key": "test", "category": "general"},
    )

    items = FinnhubNewsProvider().fetch_items(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].external_id == "42"
    assert items[0].title == "Fed decision moves markets"


def test_polygon_provider_maps_ticker_news(monkeypatch) -> None:
    monkeypatch.setattr(
        "news_agent.ingestion.providers._fetch_json",
        lambda url, timeout_seconds: {
            "results": [
                {
                    "id": "abc",
                    "title": "Cloud capex lifts suppliers",
                    "article_url": "https://example.com/cloud",
                    "description": "AI infrastructure spending rose.",
                    "published_utc": "2026-05-29T14:30:00Z",
                    "publisher": {"name": "Example Wire"},
                    "tickers": ["NVDA", "AVGO"],
                }
            ]
        },
    )
    source = SimpleNamespace(
        id=5,
        provider="polygon",
        external_account="NVDA",
        config={"api_key": "test", "ticker": "NVDA"},
    )

    items = PolygonNewsProvider().fetch_items(source, timeout_seconds=5)

    assert len(items) == 1
    assert items[0].external_id == "abc"
    assert items[0].metadata["tickers"] == ["NVDA", "AVGO"]
