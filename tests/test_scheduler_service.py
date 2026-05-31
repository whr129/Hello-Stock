from datetime import UTC, datetime, timedelta

import pytest

from news_agent.scheduler.service import (
    RefreshSummary,
    SchedulerControlService,
    _due_pipelines,
    parse_config_value,
    run_scheduler_tick,
)
from news_agent.settings import Settings


def test_parse_config_value_coerces_primitives() -> None:
    assert parse_config_value("true") is True
    assert parse_config_value("12") == 12
    assert parse_config_value("3.5") == 3.5
    assert parse_config_value("feed") == "feed"


def test_refresh_retry_settings_have_defaults() -> None:
    settings = Settings(openai_api_key="")

    assert settings.source_fetch_max_attempts == 3
    assert settings.source_fetch_retry_backoff_seconds == 2
    assert settings.market_fetch_max_attempts == 2
    assert settings.market_fetch_retry_backoff_seconds == 2
    assert settings.refresh_report_enabled is True


def test_format_refresh_summary_includes_provider_counts() -> None:
    service = SchedulerControlService(Settings(openai_api_key=""))
    summary = RefreshSummary(
        job_type="manual_refresh",
        saved_article_count=4,
        summary_count=2,
        market_snapshot_count=3,
        error_count=1,
        provider_counts={"rss": 5, "twitter": 2},
        errors=["Business Feed: timeout"],
    )

    text = service.format_refresh_summary(summary)

    assert "Articles saved: 4" in text
    assert "rss: 5" in text
    assert "twitter: 2" in text
    assert "Error details:" in text
    assert "Business Feed: timeout" in text


@pytest.mark.asyncio
async def test_can_start_refresh_recovers_stale_running_jobs(monkeypatch) -> None:
    calls: list[str] = []

    class FakeJobRepository:
        def __init__(self, session) -> None:
            del session

        async def recover_stale_running_jobs(self, cutoff):
            del cutoff
            calls.append("recover")
            return 1

        async def has_running_job(self) -> bool:
            calls.append("check")
            return False

    class FakeSession:
        async def commit(self):
            calls.append("commit")

    class FakeSessionContext:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

    monkeypatch.setattr("news_agent.scheduler.service.JobRepository", FakeJobRepository)

    service = SchedulerControlService(Settings(openai_api_key=""))
    service.session_factory = lambda: FakeSessionContext()

    assert await service.can_start_refresh() is True
    assert calls == ["recover", "commit", "check"]


def test_due_pipelines_respects_intervals_and_market_hours() -> None:
    settings = Settings(openai_api_key="")
    now = datetime(2026, 5, 29, 14, 0, tzinfo=UTC)
    last_runs = {
        "market_prices": now - timedelta(seconds=599),
        "breaking_resources": now - timedelta(seconds=1800),
        "daily_resources": now - timedelta(seconds=3600),
    }

    assert _due_pipelines(settings, last_runs, now) == ["breaking_resources"]


def test_due_pipelines_adds_prices_only_when_market_is_open() -> None:
    settings = Settings(openai_api_key="")

    assert "market_prices" in _due_pipelines(
        settings,
        {},
        datetime(2026, 5, 29, 14, 0, tzinfo=UTC),
    )
    assert "market_prices" not in _due_pipelines(
        settings,
        {},
        datetime(2026, 5, 30, 14, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_run_scheduler_tick_triggers_due_tiered_pipelines(monkeypatch) -> None:
    calls: list[str] = []

    class FakeSchedulerControlService:
        session_factory = object()

        def __init__(self, settings) -> None:
            del settings

        async def can_start_refresh(self) -> bool:
            return True

        async def run_refresh(self, job_type: str = "manual_refresh"):
            calls.append(job_type)
            return RefreshSummary(job_type, 0, 0, 0, 0, {}, [])

        async def cleanup_expired_content(self):
            calls.append("cleanup")
            return {}

    class FakeMemoryConsolidationService:
        def __init__(self, session_factory, settings) -> None:
            del session_factory, settings

        async def process_due_jobs(self):
            calls.append("memory")

    monkeypatch.setattr(
        "news_agent.scheduler.service.SchedulerControlService",
        FakeSchedulerControlService,
    )
    monkeypatch.setattr(
        "news_agent.scheduler.service.MemoryConsolidationService",
        FakeMemoryConsolidationService,
    )

    result = await run_scheduler_tick(
        Settings(openai_api_key=""),
        {},
        now=datetime(2026, 5, 29, 14, 0, tzinfo=UTC),
    )

    assert calls == ["market_prices", "breaking_resources", "daily_resources", "memory", "cleanup"]
    assert set(result) == {"market_prices", "breaking_resources", "daily_resources"}
