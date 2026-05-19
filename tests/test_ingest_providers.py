from types import SimpleNamespace

import pytest

from news_agent.ingestion.providers import AccountFeedProvider, RSSIngestProvider


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
