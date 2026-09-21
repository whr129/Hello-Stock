from pathlib import Path

import pytest
from pydantic import ValidationError

from news_agent.settings import Settings


def test_example_environment_loads_with_runtime_alerts_disabled(monkeypatch) -> None:
    monkeypatch.delenv("RUNTIME_ALERT_TELEGRAM_CHAT_ID", raising=False)

    settings = Settings(_env_file=Path(__file__).resolve().parents[2] / ".env.example")

    assert settings.runtime_alert_telegram_chat_id == 0


def test_daily_report_configuration_loads_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_RESEARCH_REPORT_ENABLED", "false")
    monkeypatch.setenv("DAILY_RESEARCH_REPORT_TIME", "09:30")
    monkeypatch.setenv("DAILY_RESEARCH_REPORT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("DAILY_RESEARCH_REPORT_CHAT_ID", "-100123456")
    monkeypatch.setenv("DAILY_RESEARCH_REPORT_MAX_CANDIDATES", "5")
    monkeypatch.setenv("DAILY_RESEARCH_REPORT_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("DAILY_RESEARCH_REPORT_RETRY_SECONDS", "600")

    settings = Settings(_env_file=None)

    assert settings.daily_research_report_enabled is False
    assert settings.daily_research_report_time == "09:30"
    assert settings.daily_research_report_timezone == "Asia/Tokyo"
    assert settings.daily_research_report_chat_id == -100123456
    assert settings.daily_research_report_max_candidates == 5
    assert settings.daily_research_report_max_attempts == 4
    assert settings.daily_research_report_retry_seconds == 600


@pytest.mark.parametrize(
    "overrides",
    [
        {"daily_research_report_time": "24:00"},
        {"daily_research_report_time": "9:00"},
        {"daily_research_report_time": "18:60"},
        {"daily_research_report_timezone": "Not/A_Zone"},
        {"daily_research_report_max_candidates": 0},
        {"daily_research_report_max_candidates": 11},
        {"daily_research_report_max_attempts": 0},
        {"daily_research_report_retry_seconds": 0},
    ],
)
def test_daily_report_rejects_invalid_schedule_and_limits(overrides) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)
