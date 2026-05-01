from datetime import UTC, datetime

import pytest

from news_agent.scheduler.service import (
    SchedulerControlService,
    RefreshSummary,
    parse_config_value,
    should_send_daily_recap,
    validate_delivery_time,
)
from news_agent.settings import Settings


def test_validate_delivery_time_normalizes_24h_time() -> None:
    assert validate_delivery_time("08:30") == "08:30"


def test_parse_config_value_coerces_primitives() -> None:
    assert parse_config_value("true") is True
    assert parse_config_value("12") == 12
    assert parse_config_value("3.5") == 3.5
    assert parse_config_value("feed") == "feed"


def test_should_send_daily_recap_only_once_per_local_day() -> None:
    now = datetime(2026, 5, 1, 13, 0, tzinfo=UTC)
    last_sent = datetime(2026, 5, 1, 12, 45, tzinfo=UTC)

    assert (
        should_send_daily_recap(
            now_utc=now,
            timezone_name="America/Toronto",
            delivery_time="08:30",
            last_sent_at=last_sent,
        )
        is False
    )


def test_should_send_daily_recap_when_time_has_arrived_and_not_sent_today() -> None:
    now = datetime(2026, 5, 1, 13, 0, tzinfo=UTC)
    last_sent = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)

    assert (
        should_send_daily_recap(
            now_utc=now,
            timezone_name="America/Toronto",
            delivery_time="08:30",
            last_sent_at=last_sent,
        )
        is True
    )


def test_format_refresh_summary_includes_provider_counts() -> None:
    service = SchedulerControlService(Settings(openai_api_key=""))
    summary = RefreshSummary(
        job_type="manual_refresh",
        saved_article_count=4,
        summary_count=2,
        market_snapshot_count=3,
        error_count=1,
        provider_counts={"rss": 5, "twitter": 2},
        errors=["Reuters Business: timeout"],
    )

    text = service.format_refresh_summary(summary)

    assert "Articles saved: 4" in text
    assert "rss: 5" in text
    assert "twitter: 2" in text
    assert "Error details:" in text
    assert "Reuters Business: timeout" in text


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
