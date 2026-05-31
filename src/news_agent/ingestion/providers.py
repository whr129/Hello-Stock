import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from news_agent.ingestion.feeds import parse_feed
from news_agent.settings import Settings
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


class AlphaVantageNewsProvider:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def fetch_items(self, source: Source, timeout_seconds: int) -> list[NormalizedIngestItem]:
        config = dict(source.config or {})
        api_key = str(config.get("api_key") or self.api_key).strip()
        if not api_key:
            raise ValueError(
                "alpha_vantage source requires ALPHA_VANTAGE_API_KEY or config.api_key"
            )

        params = {
            "function": "NEWS_SENTIMENT",
            "apikey": api_key,
            "limit": int(config.get("limit") or config.get("max_items") or 50),
        }
        tickers = _csv_config(config, "tickers")
        topics = _csv_config(config, "topics")
        if tickers:
            params["tickers"] = ",".join(tickers)
        if topics:
            params["topics"] = ",".join(topics)
        if config.get("sort"):
            params["sort"] = str(config["sort"])
        if config.get("time_from"):
            params["time_from"] = str(config["time_from"])

        payload = _fetch_json(
            "https://www.alphavantage.co/query?" + urlencode(params),
            timeout_seconds,
        )
        items = payload.get("feed") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return []
        return [
            NormalizedIngestItem(
                external_id=str(item.get("url") or item.get("title") or ""),
                url=str(item.get("url") or ""),
                title=str(item.get("title") or "Untitled"),
                body_text=str(item.get("summary") or "") or None,
                published_at=_parse_alpha_vantage_time(item.get("time_published")),
                author=str(item.get("source") or "") or None,
                raw_payload=dict(item),
                provider=source.provider,
                account=source.external_account,
                metadata={
                    "source_id": source.id,
                    "provider_source": item.get("source"),
                    "topics": item.get("topics"),
                    "ticker_sentiment": item.get("ticker_sentiment"),
                },
            )
            for item in items
            if isinstance(item, dict) and item.get("url")
        ]


class FinnhubNewsProvider:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def fetch_items(self, source: Source, timeout_seconds: int) -> list[NormalizedIngestItem]:
        config = dict(source.config or {})
        api_key = str(config.get("api_key") or self.api_key).strip()
        if not api_key:
            raise ValueError("finnhub source requires FINNHUB_API_KEY or config.api_key")

        symbol = str(config.get("symbol") or "").strip().upper()
        if symbol:
            endpoint = "https://finnhub.io/api/v1/company-news"
            params = {
                "symbol": symbol,
                "from": str(config.get("from") or _recent_date(days=7)),
                "to": str(config.get("to") or _recent_date(days=0)),
                "token": api_key,
            }
        else:
            endpoint = "https://finnhub.io/api/v1/news"
            params = {
                "category": str(config.get("category") or "general"),
                "token": api_key,
            }
        payload = _fetch_json(endpoint + "?" + urlencode(params), timeout_seconds)
        items = payload if isinstance(payload, list) else []
        return [
            NormalizedIngestItem(
                external_id=str(item.get("id") or item.get("url") or ""),
                url=str(item.get("url") or ""),
                title=str(item.get("headline") or "Untitled"),
                body_text=str(item.get("summary") or "") or None,
                published_at=_parse_unix_time(item.get("datetime")),
                author=str(item.get("source") or "") or None,
                raw_payload=dict(item),
                provider=source.provider,
                account=source.external_account,
                metadata={"source_id": source.id, "provider_source": item.get("source")},
            )
            for item in items
            if isinstance(item, dict) and item.get("url")
        ]


class PolygonNewsProvider:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def fetch_items(self, source: Source, timeout_seconds: int) -> list[NormalizedIngestItem]:
        config = dict(source.config or {})
        api_key = str(config.get("api_key") or self.api_key).strip()
        if not api_key:
            raise ValueError("polygon source requires POLYGON_API_KEY or config.api_key")

        params = {
            "apiKey": api_key,
            "limit": int(config.get("limit") or config.get("max_items") or 50),
            "order": str(config.get("order") or "desc"),
            "sort": str(config.get("sort") or "published_utc"),
        }
        ticker = str(config.get("ticker") or source.external_account or "").strip().upper()
        if ticker:
            params["ticker"] = ticker
        payload = _fetch_json(
            "https://api.polygon.io/v2/reference/news?" + urlencode(params),
            timeout_seconds,
        )
        items = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return []
        return [
            NormalizedIngestItem(
                external_id=str(item.get("id") or item.get("article_url") or ""),
                url=str(item.get("article_url") or ""),
                title=str(item.get("title") or "Untitled"),
                body_text=str(item.get("description") or "") or None,
                published_at=_parse_rfc3339(item.get("published_utc")),
                author=str(item.get("author") or item.get("publisher", {}).get("name") or "")
                or None,
                raw_payload=dict(item),
                provider=source.provider,
                account=source.external_account,
                metadata={
                    "source_id": source.id,
                    "publisher": item.get("publisher"),
                    "tickers": item.get("tickers"),
                },
            )
            for item in items
            if isinstance(item, dict) and item.get("article_url")
        ]


class IngestProviderRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        self.providers: dict[str, IngestProvider] = {
            "rss": RSSIngestProvider(),
            "twitter": AccountFeedProvider("twitter"),
            "newsletter": AccountFeedProvider("newsletter"),
            "alpha_vantage": AlphaVantageNewsProvider(
                settings.alpha_vantage_api_key if settings else ""
            ),
            "finnhub": FinnhubNewsProvider(settings.finnhub_api_key if settings else ""),
            "polygon": PolygonNewsProvider(settings.polygon_api_key if settings else ""),
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


def _fetch_json(url: str, timeout_seconds: int) -> object:
    request = Request(url, headers={"User-Agent": "news-agent/0.1 market-research"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _csv_config(config: dict, key: str) -> list[str]:
    value = config.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _parse_alpha_vantage_time(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def _parse_unix_time(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _parse_rfc3339(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None


def _recent_date(*, days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
