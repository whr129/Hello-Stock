from pathlib import Path

from news_agent.settings import Settings


def test_example_environment_loads_with_runtime_alerts_disabled(monkeypatch) -> None:
    monkeypatch.delenv("RUNTIME_ALERT_TELEGRAM_CHAT_ID", raising=False)

    settings = Settings(_env_file=Path(__file__).resolve().parents[2] / ".env.example")

    assert settings.runtime_alert_telegram_chat_id == 0
