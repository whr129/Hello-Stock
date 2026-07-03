from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from news_agent.storage.repositories import ArticleRepository, SummaryRepository


@pytest.mark.asyncio
async def test_summary_cleanup_skips_summaries_referenced_by_market_mentions() -> None:
    session = _FakeSession()

    deleted = await SummaryRepository(session).delete_created_before(
        datetime(2026, 6, 1, tzinfo=UTC)
    )

    assert deleted == 2
    sql = _compiled_sql(session.statement)
    assert "DELETE FROM summaries" in sql
    assert "summaries.id NOT IN" in sql
    assert "market_mentions.summary_id" in sql


@pytest.mark.asyncio
async def test_article_cleanup_skips_articles_referenced_by_summaries_or_market_mentions() -> None:
    session = _FakeSession()

    deleted = await ArticleRepository(session).delete_created_before(
        datetime(2026, 6, 1, tzinfo=UTC)
    )

    assert deleted == 2
    sql = _compiled_sql(session.statement)
    assert "DELETE FROM articles" in sql
    assert "articles.id NOT IN" in sql
    assert "summaries.article_id" in sql
    assert "market_mentions.article_id" in sql


def _compiled_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


class _FakeSession:
    def __init__(self) -> None:
        self.statement = None
        self.flushed = False

    async def execute(self, statement):
        self.statement = statement
        return _FakeResult()

    async def flush(self):
        self.flushed = True


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return [1, 2]
