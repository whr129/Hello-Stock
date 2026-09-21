from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from news_agent.storage.repositories import (
    ArticleRepository,
    RuntimeRunRepository,
    SummaryRepository,
)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("acquired", [True, False])
async def test_daily_report_lock_is_nonblocking_and_transaction_scoped(acquired: bool) -> None:
    result = MagicMock()
    result.scalar.return_value = acquired
    session = AsyncMock()
    session.execute.return_value = result

    assert await RuntimeRunRepository(session).lock_daily_research_report() is acquired

    compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "pg_try_advisory_xact_lock(" in str(compiled)
    assert list(compiled.params.values()) == [0x4E415250]
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_report_lookup_scopes_date_and_workflow_and_returns_latest() -> None:
    run = object()
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    session = AsyncMock()
    session.execute.return_value = result

    assert await RuntimeRunRepository(session).get_daily_research_report("2026-09-20") is run

    compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "runtime_runs.workflow =" in sql
    assert "runtime_runs.trigger =" in sql
    assert "ORDER BY runtime_runs.id DESC" in sql
    assert "LIMIT" in sql
    assert set(compiled.params.values()) == {"daily_research_report", "2026-09-20", 1}


@pytest.mark.asyncio
async def test_runtime_cleanup_preserves_recent_daily_report_delivery_records() -> None:
    session = _FakeSession()
    cutoff = datetime.now(UTC)

    deleted = await RuntimeRunRepository(session).delete_started_before(cutoff)

    compiled = session.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert deleted == 2
    assert session.flushed
    assert "DELETE FROM runtime_runs" in sql
    assert "runtime_runs.started_at <" in sql
    assert "AND (runtime_runs.workflow !=" in sql
    assert "OR runtime_runs.started_at <" in sql
    assert "RETURNING runtime_runs.id" in sql
    params = compiled.params
    assert params["started_at_1"] == cutoff
    assert params["workflow_1"] == "daily_research_report"
    assert cutoff - timedelta(days=2) <= params["started_at_2"] <= (
        datetime.now(UTC) - timedelta(days=2)
    )


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
