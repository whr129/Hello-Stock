from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from news_agent.graph.nodes import _source_has_required_credentials
from news_agent.ingestion.providers import _parse_unix_time
from news_agent.settings import Settings
from news_agent.storage.repositories import SourceRepository


class IndexTimestamp:
    def __index__(self) -> int:
        return 1


@pytest.mark.parametrize(
    "value", [1, 1.0, "1", b"1", bytearray(b"1"), memoryview(b"1"), Decimal(1), IndexTimestamp()]
)
def test_unix_time_preserves_numeric_coercions(value) -> None:
    assert _parse_unix_time(value) == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)


@pytest.mark.parametrize("value", [None, {}, [], object(), "invalid"])
def test_unix_time_rejects_invalid_values(value) -> None:
    assert _parse_unix_time(value) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [{"api_key": "test"}, [("api_key", "test")]])
async def test_source_defaults_preserve_config_and_numeric_coercions(config) -> None:
    repository = SourceRepository(AsyncMock())
    repository.add_source = AsyncMock()
    source = {
        "provider": "finnhub",
        "external_account": "NVDA",
        "config": config,
        "trust_score": Decimal("0.75"),
    }

    assert _source_has_required_credentials(source, Settings(finnhub_api_key=""))
    await repository.ensure_default_sources([source])

    assert repository.add_source.await_args.kwargs["config"] == {"api_key": "test"}
    assert repository.add_source.await_args.kwargs["trust_score"] == 0.75


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["config", "trust_score"])
async def test_source_defaults_reject_invalid_types_before_insertion(field) -> None:
    repository = SourceRepository(AsyncMock())
    repository.add_source = AsyncMock()
    source = {"provider": "rss", "feed_url": "https://example.com/rss", field: object()}

    with pytest.raises(TypeError):
        await repository.ensure_default_sources([source])

    repository.add_source.assert_not_awaited()
    if field == "config":
        with pytest.raises(TypeError):
            _source_has_required_credentials(source, Settings())
