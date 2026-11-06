from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.error import HTTPError

import pytest

from news_agent.ingestion.errors import http_error_status
from news_agent.storage.repositories import SourceRepository


@pytest.mark.asyncio
async def test_only_repeated_typed_unauthorized_responses_disable_source():
    source = SimpleNamespace(config={}, enabled=True, last_error=None, last_fetched_at=None)
    repository = SourceRepository(AsyncMock())
    repository.get_by_id = AsyncMock(return_value=source)
    for status in (401, 403, 401):
        await repository.mark_fetch_result(
            1, fetched_at=datetime.now(UTC), success=False, error="unavailable",
            http_status=status, disable_unauthorized=True,
        )
        assert source.enabled
    await repository.mark_fetch_result(
        1, fetched_at=datetime.now(UTC), success=False, error="unauthorized",
        http_status=401, disable_unauthorized=True,
    )
    assert not source.enabled
    assert source.config["quarantined_http_status"] == 401


def test_error_status_does_not_infer_401_from_text():
    assert http_error_status(ValueError("401 in story title")) is None
    error = HTTPError("https://example.test", 401, "Unauthorized", {}, None)
    assert http_error_status(error) == 401


@pytest.mark.asyncio
async def test_default_source_seeding_does_not_reactivate_quarantined_source():
    source = SimpleNamespace(
        enabled=False, provider="rss", external_account="https://example.test/feed",
        config={"quarantined_http_status": 401}, field_mapping={},
        fetch_mode="rss", trust_score=0.5,
    )
    session = AsyncMock()
    result = SimpleNamespace(scalar_one_or_none=lambda: source)
    session.execute.return_value = result
    repository = SourceRepository(session)
    await repository.add_source(
        name="Example", provider="rss", external_account="https://example.test/feed",
        reactivate=False,
    )
    assert not source.enabled
    assert source.config["quarantined_http_status"] == 401
