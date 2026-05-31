import os

import pytest

from news_agent.maintenance.validate_sources import validate_source_pack


@pytest.mark.skipif(
    os.getenv("LIVE_SOURCE_VALIDATION") != "1",
    reason="external source validation is opt-in",
)
def test_default_source_pack_live_sources_fetch() -> None:
    result = validate_source_pack(timeout_seconds=10)

    assert result["dropped"] == 0
