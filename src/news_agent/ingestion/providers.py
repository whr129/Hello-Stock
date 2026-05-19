from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from news_agent.ingestion.feeds import parse_feed
from news_agent.storage.models import Source


@dataclass(frozen=True)
class NormalizedIngestItem:
    external_id: str
    url: str
    title: str
    body_text: str | None
    published_at: datetime | None
    author: str | None
    raw_payload: dict
    provider: str
    account: str
    metadata: dict


class IngestProvider(Protocol):
    def fetch_items(self, source: Source, timeout_seconds: int) -> list[NormalizedIngestItem]:
        raise NotImplementedError


class RSSIngestProvider:
    def fetch_items(self, source: Source, timeout_seconds: int) -> list[NormalizedIngestItem]:
        feed_url = str(
            (source.config or {}).get("feed_url") or source.external_account or source.url
        )
        if not feed_url:
            raise ValueError("rss source requires a feed URL before it can be fetched")
        articles = parse_feed(feed_url, timeout_seconds=timeout_seconds)
        return [
            _map_feed_article(
                source,
                article.raw_payload,
                article.url,
                article.title,
                article.summary,
                article.published_at,
                article.author,
            )
            for article in articles
        ]


class AccountFeedProvider:
    """Generic account-based provider backed by a configured feed URL plus field mapping."""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name

    def fetch_items(self, source: Source, timeout_seconds: int) -> list[NormalizedIngestItem]:
        feed_url = str((source.config or {}).get("feed_url") or "")
        if not feed_url:
            raise ValueError(
                f"{self.provider_name} source requires config.feed_url before it can be fetched"
            )
        articles = parse_feed(feed_url, timeout_seconds=timeout_seconds)
        return [
            _map_feed_article(
                source,
                article.raw_payload,
                article.url,
                article.title,
                article.summary,
                article.published_at,
                article.author,
            )
            for article in articles
        ]


class IngestProviderRegistry:
    def __init__(self) -> None:
        self.providers: dict[str, IngestProvider] = {
            "rss": RSSIngestProvider(),
            "twitter": AccountFeedProvider("twitter"),
            "newsletter": AccountFeedProvider("newsletter"),
        }

    def get(self, provider: str) -> IngestProvider:
        normalized = provider.strip().lower()
        if normalized not in self.providers:
            raise ValueError(f"Unsupported source provider: {provider}")
        return self.providers[normalized]


def _map_feed_article(
    source: Source,
    raw_payload: dict,
    fallback_url: str,
    fallback_title: str,
    fallback_summary: str | None,
    fallback_published_at: datetime | None,
    fallback_author: str | None,
) -> NormalizedIngestItem:
    mapping = dict(source.field_mapping or {})
    url = _mapped_string(raw_payload, mapping.get("url_field")) or fallback_url
    title = _mapped_string(raw_payload, mapping.get("title_field")) or fallback_title
    body_text = _mapped_string(raw_payload, mapping.get("body_field")) or fallback_summary
    author = _mapped_string(raw_payload, mapping.get("author_field")) or fallback_author
    external_id = _mapped_string(raw_payload, mapping.get("external_id_field")) or url
    return NormalizedIngestItem(
        external_id=external_id,
        url=url,
        title=title,
        body_text=body_text,
        published_at=fallback_published_at,
        author=author,
        raw_payload=raw_payload,
        provider=source.provider,
        account=source.external_account,
        metadata={"source_id": source.id},
    )


def _mapped_string(payload: dict, field_name: object) -> str | None:
    if not isinstance(field_name, str) or not field_name.strip():
        return None
    value = payload.get(field_name)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
