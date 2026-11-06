"""Resolve the alert profile before any scheduling or delivery decision."""

from news_agent.harness.catalog import ALERT_ASSESS_CAPABILITY
from news_agent.harness.config import load_harness_config
from news_agent.harness.service import effective_settings
from news_agent.settings import Settings


def resolve_alert_settings(settings: Settings) -> Settings:
    config = load_harness_config(
        settings.harness_config_file or None,
        defaults={"profiles": {"alerts": {
            "allowed_capabilities": [ALERT_ASSESS_CAPABILITY.id],
            "exposed_tools": [],
            "memory": "disabled",
        }}},
    )
    return effective_settings(settings, config, "alerts")
