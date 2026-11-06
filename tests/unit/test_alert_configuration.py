from news_agent.alerts.config import resolve_alert_settings
from news_agent.settings import Settings, get_settings


def test_alert_profile_is_applied_before_enabled_and_polling_guards(tmp_path):
    path = tmp_path / "harness.toml"
    path.write_text(
        "schema_version = 1\n"
        "[profiles.alerts.settings]\n"
        "market_alerts_enabled = true\n"
        "market_alert_poll_seconds = 20\n"
        "jev_monthly_budget_usd = 2.5\n"
    )
    settings = resolve_alert_settings(Settings(_env_file=None, harness_config_file=str(path)))
    assert settings.market_alerts_enabled
    assert settings.market_alert_poll_seconds == 20
    assert settings.jev_monthly_budget_usd == 2.5


def test_global_harness_settings_apply_to_process_startup(monkeypatch, tmp_path):
    path = tmp_path / "harness.toml"
    path.write_text("schema_version = 1\n[settings]\nnews_fetch_interval_seconds = 123\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HARNESS_CONFIG_FILE", str(path))
    monkeypatch.delenv("NEWS_FETCH_INTERVAL_SECONDS", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().news_fetch_interval_seconds == 123
    finally:
        get_settings.cache_clear()
