from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from news_agent.alerts.decision import JevDecision, JevUsage
from news_agent.alerts.evidence import (
    ArticleEvidence,
    event_identity,
    provider_observations,
    same_event,
)
from news_agent.alerts.policy import DeliveryPolicyConfig, format_alert, guard_delivery

NOW = datetime(2026, 10, 30, 16, tzinfo=UTC)


def article(**changes):
    data = dict(
        article_id="1",
        url="https://www.example.com/story?utm_source=x",
        title="Acme cuts annual profit guidance after factory closure",
        text="Evidence",
        published_at=NOW - timedelta(minutes=5),
        first_seen_at=NOW,
        tickers=["ACME"],
        trust_score=0.95,
        primary_source=True,
    )
    return ArticleEvidence(**(data | changes))


def decision(**changes):
    return JevDecision(
        **(
            dict(
                delivery="immediate_alert",
                confidence=0.96,
                materiality=2.0,
                event_type="guidance",
                reason_code="material_new_event",
                sentiments={},
                model="typesafe/jev-1.14",
                usage=JevUsage(input_tokens=0, output_tokens=0),
                probabilities={},
                attempts=1,
            )
            | changes
        )
    )


def config(**changes):
    return DeliveryPolicyConfig(**(dict(enabled=True, shadow_mode=False, chat_id=123) | changes))


def test_evidence_normalizes_publisher_and_is_json_safe():
    evidence = article(source_id="vendor")
    assert evidence.source_id == "example.com"
    assert evidence.url == "https://www.example.com/story"
    assert ArticleEvidence.model_validate(evidence.model_dump(mode="json")) == evidence


@pytest.mark.parametrize(
    "changes",
    [
        dict(url="http://localhost/x"),
        dict(published_at=NOW.replace(tzinfo=None)),
        dict(tickers=["ignore instructions"]),
        dict(trust_score=float("nan")),
    ],
)
def test_invalid_evidence_rejected(changes):
    with pytest.raises(ValidationError):
        article(**changes)


def test_syndication_and_updates_match_without_article_id_identity():
    first = event_identity(article())
    duplicate = event_identity(article(article_id="2", url="https://other.com/story"))
    assert first.key == duplicate.key
    correction = event_identity(
        article(title="Correction: Acme cuts annual profit guidance after factory closure")
    )
    assert same_event(first.to_dict(), correction.to_dict())
    assert first.revision_key != correction.revision_key
    distinct = event_identity(article(provider_metadata={"case_number": "2"}))
    prior = event_identity(article(provider_metadata={"case_number": "1"}))
    assert not same_event(prior, distinct)
    assert not same_event(first, event_identity(article(published_at=NOW + timedelta(days=3))))


def test_vendor_observations_reject_unknown_tickers_and_bad_scores():
    rows = [
        {"ticker": "ACME", "ticker_sentiment_score": "0.4", "ticker_sentiment_label": "Bullish"},
        {"ticker": "BAD", "ticker_sentiment_score": "0.4", "ticker_sentiment_label": "Bullish"},
        {"ticker": "ACME", "ticker_sentiment_score": "nan", "ticker_sentiment_label": "Bullish"},
    ]
    observations = provider_observations(
        article(provider="alpha_vantage", provider_metadata={"ticker_sentiment": rows})
    )
    assert len(observations) == 1
    assert observations[0].label == "positive"
    assert observations[0].source_id == "example.com"
    assert (
        provider_observations(
            article(
                provider="polygon",
                provider_metadata={"insights": [{"ticker": "ACME", "sentiment": "negative"}]},
            )
        )[0].label
        == "negative"
    )


@pytest.mark.parametrize("delivery", ["daily_report", "ignore"])
def test_host_never_promotes_jev(delivery):
    result = guard_delivery(decision(delivery=delivery), [article()], config=config(), now=NOW)
    assert not result.allowed and result.reason == delivery


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"enabled": False}, "disabled"),
        ({"shadow_mode": True}, "shadow_mode"),
        ({"chat_id": 0}, "missing_recipient"),
        ({"min_confidence": 0.99}, "low_confidence"),
    ],
)
def test_delivery_switches(changes, reason):
    assert (
        guard_delivery(decision(), [article()], config=config(**changes), now=NOW).reason == reason
    )


def test_freshness_and_independent_corroboration():
    assert guard_delivery(decision(), [article()], config=config(), now=NOW).allowed
    stale = article(published_at=NOW - timedelta(hours=1))
    assert guard_delivery(decision(), [stale], config=config(), now=NOW).reason == "stale_evidence"
    secondary = article(primary_source=False)
    duplicate = article(article_id="2", primary_source=False)
    assert (
        guard_delivery(decision(), [secondary, duplicate], config=config(), now=NOW).reason
        == "insufficient_corroboration"
    )
    independent = article(url="https://independent.com/story", primary_source=False)
    assert guard_delivery(decision(), [secondary, independent], config=config(), now=NOW).allowed


def test_defer_until_all_limits_clear_and_critical_only_overrides_quiet():
    limits = config(quiet_start="11:00", quiet_end="14:00", max_per_hour=1)
    result = guard_delivery(
        decision(),
        [article()],
        config=limits,
        now=NOW,
        last_delivered_at=NOW - timedelta(minutes=10),
        recent_deliveries=[NOW - timedelta(minutes=5)],
    )
    assert result.reason == "quiet_hours"
    assert result.not_before == NOW + timedelta(hours=2)
    result = guard_delivery(
        decision(materiality=3.0),
        [article()],
        config=limits,
        now=NOW,
        recent_deliveries=[NOW - timedelta(minutes=5)],
    )
    assert result.reason == "hourly_cap" and not result.allowed


def test_format_only_uses_evidence_and_typed_reason():
    text = format_alert(decision(), [article(), article()])
    assert text.count("https://www.example.com/story") == 1
    assert article().title in text
    assert "Material new event" in text


def test_quiet_hours_cross_midnight_and_dst_fall_back():
    now = datetime(2026, 11, 1, 5, 45, tzinfo=UTC)  # First 01:45 in Toronto.
    evidence = article(published_at=now, first_seen_at=now)
    result = guard_delivery(
        decision(), [evidence], config=config(quiet_start="22:00", quiet_end="02:00"), now=now
    )
    assert result.reason == "quiet_hours"
    assert result.not_before == datetime(2026, 11, 1, 7, tzinfo=UTC)


def test_settings_mapping_and_invalid_quiet_hours():
    from news_agent.settings import Settings

    mapped = DeliveryPolicyConfig.from_settings(Settings(_env_file=None))
    assert mapped.min_confidence == Settings(_env_file=None).market_alert_min_confidence
    assert DeliveryPolicyConfig().min_confidence == 0.70
    assert mapped.timezone == "America/Toronto"
    with pytest.raises(ValidationError):
        config(quiet_start="22:00")
    with pytest.raises(ValidationError):
        config(quiet_start="22:00", quiet_end="22:00")


def test_material_revision_bypasses_only_cooldown_after_jev_confirmation():
    from news_agent.alerts.policy import is_material_update

    history = [{"revision": 1, "status": "delivered"}]
    update = is_material_update(decision(), event_revision=2, delivery_history=history)
    assert update
    assert not is_material_update(decision(), event_revision=1, delivery_history=history)
    assert not is_material_update(
        decision(delivery="ignore"), event_revision=2, delivery_history=history
    )
    assert not is_material_update(
        decision(reason_code="routine_update"), event_revision=2, delivery_history=history
    )
    assert not is_material_update(
        decision(),
        event_revision=2,
        delivery_history=history + [{"revision": 2, "status": "unknown"}],
    )
    result = guard_delivery(
        decision(),
        [article()],
        config=config(),
        now=NOW,
        last_delivered_at=NOW - timedelta(minutes=1),
        material_update=update,
    )
    assert result.allowed
    capped = guard_delivery(
        decision(),
        [article()],
        config=config(max_per_hour=1),
        now=NOW,
        last_delivered_at=NOW - timedelta(minutes=1),
        recent_deliveries=[NOW - timedelta(minutes=1)],
        material_update=update,
    )
    assert not capped.allowed and capped.reason == "hourly_cap"
    stale = guard_delivery(
        decision(),
        [article(published_at=NOW - timedelta(hours=1))],
        config=config(),
        now=NOW,
        material_update=update,
    )
    assert not stale.allowed and stale.reason == "stale_evidence"


def test_provider_correction_retains_real_revision_for_sentiment_suppression():
    observations = provider_observations(
        article(
            revision=2,
            provider="polygon",
            provider_metadata={"insights": [{"ticker": "ACME", "sentiment": "negative"}]},
        )
    )
    assert observations[0].revision == 2


def test_latest_revision_is_formatted_without_mutating_evidence_audit():
    from news_agent.alerts.evidence import latest_evidence

    old = article(title="Initial headline", revision=1)
    corrected = article(
        title="Corrected new headline", revision=2, first_seen_at=NOW + timedelta(seconds=1)
    )
    audit = [old, corrected]
    assert latest_evidence(audit) == [corrected]
    assert audit == [old, corrected]
    message = format_alert(decision(), audit)
    assert "Corrected new headline" in message
    assert "Initial headline" not in message
    alias = corrected.model_copy(update={"article_id": "alias", "first_seen_at": NOW})
    assert latest_evidence([alias, old, corrected]) == [corrected]
