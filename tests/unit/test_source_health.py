from datetime import UTC, datetime, timedelta

from news_agent.research.source_health import (
    score_source_health,
    source_is_suppressed,
    update_source_health_metrics,
)


def test_source_health_scores_high_impact_successful_source() -> None:
    config = update_source_health_metrics(
        {},
        fetched=10,
        accepted=8,
        saved=5,
        link_checked=5,
        link_available=5,
    )

    health = score_source_health(
        config=config,
        last_success_at=datetime.now(UTC),
        last_fetched_at=datetime.now(UTC),
        last_error=None,
    )

    assert health.score > 70
    assert health.status == "healthy"


def test_source_health_suppresses_low_signal_stale_source() -> None:
    config = update_source_health_metrics({}, fetched=20, rejected=20)

    suppressed = source_is_suppressed(
        config=config,
        last_success_at=datetime.now(UTC) - timedelta(days=4),
        last_fetched_at=datetime.now(UTC) - timedelta(days=4),
        last_error="empty feed",
        minimum_score=60,
    )

    assert suppressed is True


def test_source_health_override_prevents_suppression() -> None:
    config = update_source_health_metrics({"source_health_override": True}, fetched=20, rejected=20)

    suppressed = source_is_suppressed(
        config=config,
        last_success_at=datetime.now(UTC) - timedelta(days=4),
        last_fetched_at=datetime.now(UTC) - timedelta(days=4),
        last_error="empty feed",
        minimum_score=60,
    )

    assert suppressed is False
