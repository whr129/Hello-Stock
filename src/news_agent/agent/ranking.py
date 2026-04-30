from datetime import UTC, datetime
from typing import Any


def rank_articles(
    articles: list[dict[str, Any]],
    topics: list[str],
    tickers: list[str],
    local_region: str | None,
) -> list[dict[str, Any]]:
    normalized_topics = {topic.lower() for topic in topics}
    normalized_tickers = {ticker.upper() for ticker in tickers}
    region = (local_region or "").lower()

    def score(article: dict[str, Any]) -> float:
        title = str(article.get("title", "")).lower()
        related_tickers = {str(item).upper() for item in article.get("related_tickers", [])}
        published_at = article.get("published_at")

        value = 0.0
        value += 2.0 * sum(1 for topic in normalized_topics if topic in title)
        value += 3.0 * len(normalized_tickers & related_tickers)
        if region and region in title:
            value += 2.0
        if isinstance(published_at, datetime):
            hours_old = max(
                (datetime.now(UTC) - published_at.astimezone(UTC)).total_seconds() / 3600,
                0,
            )
            value += max(0, 24 - hours_old) / 24
        return value

    return sorted(articles, key=score, reverse=True)
