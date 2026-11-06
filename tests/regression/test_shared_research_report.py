from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from news_agent.harness.contracts import HarnessRequest
from news_agent.harness.service import StockNewsHarness
from news_agent.research import service
from news_agent.scheduler.reports import DailyResearchReportService
from news_agent.settings import Settings


def report_setup(monkeypatch, **overrides):
    session = AsyncMock()
    session.add = Mock()
    session.get.return_value = None
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=None))
    session.__aenter__.return_value = session
    repository = Mock(fetch_top_candidates=AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "MarketSignalRepository", lambda _: repository)
    monkeypatch.setattr(service, "validate_candidate_links", AsyncMock(return_value=[]))
    settings = Settings(
        _env_file=None, openai_api_key="", market_alerts_enabled=False, **overrides,
    )
    return lambda: session, settings


@pytest.mark.asyncio
async def test_fixed_daily_report_data_matches_across_channel_profiles(monkeypatch):
    factory, settings = report_setup(monkeypatch)
    harness = StockNewsHarness(factory, settings, environ={})
    as_of = datetime(2026, 10, 30, 22, tzinfo=UTC)
    outputs = []
    for profile in ("interactive", "scheduled", "cli"):
        context = harness.context(principal="test", profile=profile).model_copy(
            update={"as_of": as_of}
        )
        result = await harness.run(
            HarnessRequest(operation="research.report", profile=profile), context,
        )
        assert result.ok
        outputs.append(result.data)
    assert outputs[0] == outputs[1] == outputs[2]
    daily = await DailyResearchReportService(factory, settings)._build_report("2026-10-30", as_of)
    assert daily == outputs[0]["response"]


@pytest.mark.asyncio
async def test_disabled_report_capability_cannot_be_bypassed_by_scheduler(monkeypatch, tmp_path):
    path = tmp_path / "harness.toml"
    path.write_text('schema_version = 1\n[tools."research.report"]\nenabled = false\n')
    factory, settings = report_setup(monkeypatch, harness_config_file=str(path))
    with pytest.raises(RuntimeError, match="denied"):
        await DailyResearchReportService(factory, settings)._build_report(
            "2026-10-30", datetime(2026, 10, 30, 22, tzinfo=UTC),
        )
