from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen

import feedparser


@dataclass(frozen=True)
class FeedArticle:
    title: str
    url: str
    published_at: datetime | None
    summary: str | None


def parse_feed(url: str, timeout_seconds: int = 15) -> list[FeedArticle]:
    request = Request(url, headers={"User-Agent": "news-agent/0.1"})
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read()

    feed = feedparser.parse(payload)
    articles: list[FeedArticle] = []
    for entry in feed.entries:
        published_at = None
        published = entry.get("published") or entry.get("updated")
        if published:
            try:
                published_at = parsedate_to_datetime(published)
            except (TypeError, ValueError):
                published_at = None

        articles.append(
            FeedArticle(
                title=entry.get("title", "Untitled"),
                url=entry.get("link", ""),
                published_at=published_at,
                summary=entry.get("summary"),
            )
        )
    return [article for article in articles if article.url]
