from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from news_agent.alerts.decision import JevDecision, JevError, JevUsage
from news_agent.alerts.evidence import ArticleEvidence
from news_agent.alerts.service import MarketAlertService
from news_agent.settings import Settings

NOW = datetime(2026, 10, 30, 16, tzinfo=UTC)


def evidence(**updates):
    return ArticleEvidence(
        **(
            dict(
                article_id="1",
                url="https://example.com/story",
                title="Acme updates guidance",
                text="Confirmed source evidence",
                tickers=["ACME"],
                published_at=NOW - timedelta(minutes=1),
                first_seen_at=NOW,
                trust_score=0.95,
                primary_source=True,
            )
            | updates
        )
    )


def judgment(delivery="immediate_alert"):
    return JevDecision(
        delivery=delivery,
        confidence=0.98,
        materiality=2.0,
        event_type="guidance",
        reason_code="material_new_event",
        sentiments={"ACME": "negative"},
        model="typesafe/jev-1.14",
        usage=JevUsage(input_tokens=100, output_tokens=0, cost=0.00001),
        probabilities={},
        attempts=1,
    )


class Result:
    def __init__(self, values=()):
        self.values = values

    def all(self):
        return list(self.values)


class Session:
    def __init__(self, tracker):
        self.tracker = tracker

    async def __aenter__(self):
        self.tracker["active"] += 1
        return self

    async def __aexit__(self, *args):
        self.tracker["active"] -= 1

    async def commit(self):
        self.tracker["commits"] += 1

    async def execute(self, *args, **kwargs):
        return Result()

    async def scalars(self, *args, **kwargs):
        return Result()


class Repository:
    def __init__(self, session):
        self.tracker = session.tracker

    async def claim_decision(self, decision_id, **kwargs):
        if self.tracker.get("claimed"):
            return None
        self.tracker["claimed"] = True
        return SimpleNamespace(
            lease_token="lease",
            input_payload=self.tracker["payload"],
            kind="shock",
            event_id=1,
            event_revision=1,
        )

    async def reserve_budget(self, *args, **kwargs):
        self.tracker["reserved"] = kwargs["amount_usd"]
        return self.tracker.get("budget", True)

    async def fail_decision(self, *args, **kwargs):
        self.tracker["failure"] = kwargs

    async def complete_decision(self, *args, **kwargs):
        self.tracker["completion"] = kwargs


@pytest.fixture
def setup():
    tracker = {"active": 0, "commits": 0}
    settings = Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        market_alerts_enabled=True,
        market_alert_shadow_mode=False,
        market_alert_chat_id=123,
    )
    client = SimpleNamespace(assess=AsyncMock(return_value=judgment()))
    service = MarketAlertService(
        lambda: Session(tracker),
        settings,
        client=client,
        started_at=NOW,
        repository_type=Repository,
    )
    tracker["payload"] = {"state": service._state([evidence()]), "tickers": ["ACME"]}
    return service, client, tracker


@pytest.mark.asyncio
async def test_claim_and_budget_commit_before_http_then_queue_once(setup):
    service, client, tracker = setup

    async def assess(*args, **kwargs):
        assert tracker["active"] == 0
        assert tracker["commits"] == 1
        assert tracker["reserved"] > 0
        return judgment()

    client.assess.side_effect = assess
    assert await service._process_decision(1, NOW) == "queued"
    completion = tracker["completion"]
    assert completion["delivery"].destination == 123
    assert completion["result"]["judgment"]["delivery"] == "immediate_alert"
    assert completion["actual_cost_usd"] == 0.00001
    assert await service._process_decision(1, NOW) == "unclaimed"
    assert client.assess.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["daily_report", "ignore"])
async def test_jev_choice_never_promoted(setup, choice):
    service, client, tracker = setup
    client.assess.return_value = judgment(choice)
    assert await service._process_decision(1, NOW) == choice
    assert tracker["completion"]["delivery"] is None
    assert tracker["completion"]["delivery_choice"] == choice


@pytest.mark.asyncio
async def test_authentication_blocks_without_retry_or_response_leak(setup):
    service, client, tracker = setup
    client.assess.side_effect = JevError("authentication", status_code=401)
    assert await service._process_decision(1, NOW) == "failed"
    assert service._auth_blocked
    assert tracker["failure"]["status"] == "blocked"
    assert tracker["failure"]["retry_at"] is None
    assert tracker["failure"]["actual_cost_usd"] is None
    assert client.assess.await_count == 1


@pytest.mark.asyncio
async def test_budget_exhaustion_does_not_call_provider(setup):
    service, client, tracker = setup
    tracker["budget"] = False
    assert await service._process_decision(1, NOW) == "budget_exhausted"
    client.assess.assert_not_awaited()
    assert tracker["failure"]["actual_cost_usd"] == 0


@pytest.mark.asyncio
async def test_expired_recovered_work_does_not_spend(setup):
    service, client, tracker = setup
    assert await service._process_decision(1, NOW + timedelta(hours=1)) == "expired"
    client.assess.assert_not_awaited()
    assert "reserved" not in tracker


@pytest.mark.asyncio
async def test_shadow_records_decision_without_delivery(setup):
    service, client, tracker = setup
    service.policy = service.policy.model_copy(update={"shadow_mode": True})
    await service._process_decision(1, NOW)
    assert tracker["completion"]["delivery"] is None
    assert tracker["completion"]["result"]["guard"]["reason"] == "shadow_mode"


@pytest.mark.asyncio
async def test_transient_failure_is_scheduled_not_retried_inside_http(setup):
    service, client, tracker = setup
    client.assess.side_effect = JevError(
        "rate_limited", status_code=429, retryable=True, retry_after_seconds=60
    )
    await service._process_decision(1, NOW)
    assert tracker["failure"]["status"] == "failed"
    assert tracker["failure"]["retry_at"] >= NOW + timedelta(seconds=60)
    assert client.assess.await_count == 1


def test_credential_rotation_creates_distinct_revision(setup):
    service, client, tracker = setup
    rotated = MarketAlertService(
        service.factory,
        service.settings.model_copy(
            update={
                "openrouter_api_key": service.settings.openrouter_api_key.__class__("new-test-key")
            }
        ),
        client=client,
    )
    assert rotated.revision != service.revision
    assert "test-key" not in service.revision


def test_evidence_never_infers_primary_from_headline_or_owned_source(setup):
    service, _, _ = setup
    article = SimpleNamespace(
        id=1,
        url="https://example.com/story",
        title="SEC guidance news",
        extracted_text="",
        related_tickers=["ACME"],
        published_at=NOW,
        created_at=NOW,
        content_hash="hash",
        category="news",
        provider_metadata={},
    )
    source = SimpleNamespace(
        owner_user_id=None,
        enabled=True,
        trust_score=0.95,
        config={},
        url="https://example.com/feed",
        category="news",
        name="News",
        provider="rss",
    )
    assert not service._evidence(article, source).primary_source
    source.owner_user_id = 4
    assert service._evidence(article, source) is None


def test_payload_is_bounded_and_retains_provenance(setup):
    service, _, _ = setup
    state = service._state([evidence(text="X" * 20000)])
    assert len(state["evidence"][0]["text"]) == 500
    assert state["evidence"][0]["article_id"] == "1"
    assert service._reservation({"state": state, "tickers": ["ACME"]}) >= 0.025


def test_same_configuration_has_stable_revision(setup):
    service, client, _ = setup
    other = MarketAlertService(service.factory, service.settings, client=client)
    assert other.revision == service.revision


@pytest.mark.asyncio
async def test_event_provenance_digest_is_stable_across_poll_order(setup):
    service, _, _ = setup
    first = evidence()
    second = evidence(article_id="2", url="https://second.example/story")
    repository = SimpleNamespace(prepare_decision=AsyncMock())
    service.repository_type = lambda session: repository
    event = SimpleNamespace(
        id=1,
        tickers=["ACME"],
        evidence=[first.model_dump(mode="json"), second.model_dump(mode="json")],
    )
    await service._prepare_event_decision(None, event, NOW)
    digest = repository.prepare_decision.call_args.kwargs["input_digest"]
    event.evidence.reverse()
    await service._prepare_event_decision(None, event, NOW + timedelta(minutes=1))
    assert repository.prepare_decision.call_args.kwargs["input_digest"] == digest


@pytest.mark.asyncio
async def test_sentiment_requires_distinct_batches_and_queues_jev_review(setup):
    service, client, tracker = setup
    service.started_at = NOW - timedelta(hours=1)
    records = []
    for index in range(12):
        prior = index < 6
        published = NOW - timedelta(hours=25) if prior else NOW - timedelta(minutes=5)
        records.append(
            evidence(
                article_id=str(index),
                url=f"https://publisher{index % 2}.example/story{index}",
                title=f"ACME story number {index}",
                provider="polygon",
                published_at=published,
                first_seen_at=published,
                provider_metadata={
                    "insights": [
                        {"ticker": "ACME", "sentiment": "positive" if prior else "negative"}
                    ]
                },
            )
        )
    service._articles = AsyncMock(side_effect=lambda *args, **kwargs: records)
    snapshots = []
    reviews = []

    class SentimentRepository:
        def __init__(self, session):
            pass

        async def list_recent_events(self, **kwargs):
            return []

        async def list_recent_decisions(self, **kwargs):
            return []

        async def latest_sentiment(self, *args):
            return snapshots[-1] if snapshots else None

        async def save_sentiment(self, **kwargs):
            snapshots.append(SimpleNamespace(**kwargs))

        async def register_event(self, *args, **kwargs):
            return SimpleNamespace(id=4)

        async def prepare_decision(self, *args, **kwargs):
            reviews.append(kwargs)

    class HealthySession(Session):
        async def scalars(self, *args, **kwargs):
            return Result(
                [
                    SimpleNamespace(
                        provider="polygon", enabled=True, last_error=None, last_success_at=NOW
                    )
                ]
            )

    service.factory = lambda: HealthySession(tracker)
    service.repository_type = SentimentRepository
    await service._sentiments(NOW)
    assert snapshots[-1].gaps == ["awaiting_confirmation"]
    assert not reviews
    await service._sentiments(NOW + timedelta(seconds=30))
    assert not reviews  # Polling the same data is not a confirmation batch.
    records.append(
        evidence(
            article_id="13",
            url="https://publisher0.example/new",
            title="ACME newly reported independent development",
            provider="polygon",
            published_at=NOW + timedelta(seconds=45),
            first_seen_at=NOW + timedelta(seconds=45),
            provider_metadata={"insights": [{"ticker": "ACME", "sentiment": "negative"}]},
        )
    )
    await service._sentiments(NOW + timedelta(minutes=1))
    assert len(reviews) == 1
    assert reviews[0]["kind"] == "sentiment"
    assert reviews[0]["input_payload"]["state"]["sentiment_windows"]["current"]["mean"] == -1
    # Durable review still goes through the paid decision lifecycle.
    client.assess.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("alert", "daily", "expected"), [(123, 456, 123), (0, 456, 456)])
async def test_destination_configuration_does_not_query_database(alert, daily, expected):
    from news_agent.alerts.destination import resolve_alert_destination

    settings = Settings(
        _env_file=None, market_alert_chat_id=alert, daily_research_report_chat_id=daily
    )

    def forbidden_factory():
        raise AssertionError("Configured destinations must not read conversation history")

    assert await resolve_alert_destination(forbidden_factory, settings) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("latest", "expected"), [(789, 789), (None, 0)])
async def test_destination_reuses_latest_chat_fallback(setup, monkeypatch, latest, expected):
    from news_agent.alerts import destination

    service, _, _ = setup
    repository = SimpleNamespace(latest_user_chat_id=AsyncMock(return_value=latest))
    monkeypatch.setattr(destination, "ConversationEventRepository", lambda session: repository)
    settings = Settings(_env_file=None, market_alert_chat_id=0, daily_research_report_chat_id=0)
    assert await destination.resolve_alert_destination(service.factory, settings) == expected
    repository.latest_user_chat_id.assert_awaited_once()


def test_sentiment_health_uses_fast_collector_state_and_fails_closed(setup):
    service, _, _ = setup
    source = SimpleNamespace(
        enabled=True,
        last_success_at=None,
        last_error="old-refresh-error",
        config={"alert_last_success_at": NOW.isoformat(), "alert_last_error": None},
    )
    assert service._source_healthy(source, NOW)
    source.config["alert_last_error"] = "http_401"
    assert not service._source_healthy(source, NOW)
    source.config = {"alert_last_success_at": "invalid"}
    assert not service._source_healthy(source, NOW)
    source.config = {"alert_last_success_at": (NOW - timedelta(hours=3)).isoformat()}
    assert not service._source_healthy(source, NOW)


def test_alert_profile_settings_and_execution_limits_are_applied(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKET_ALERTS_ENABLED", raising=False)
    monkeypatch.delenv("JEV_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.delenv("SOURCE_DEFAULT_FETCH_INTERVAL_SECONDS", raising=False)
    config = tmp_path / "alerts.toml"
    config.write_text("""[settings]
market_alerts_enabled = true
[profiles.alerts]
allowed_capabilities = ["alerts.assess"]
[profiles.alerts.settings]
market_alerts_enabled = false
jev_monthly_budget_usd = 0.02
source_default_fetch_interval_seconds = 900
[profiles.alerts.execution]
max_parallel_calls = 1
deadline_seconds = 20
""")
    settings = Settings(_env_file=None, harness_config_file=str(config))
    service = MarketAlertService(None, settings, client=SimpleNamespace())
    assert not service.settings.market_alerts_enabled
    assert service.settings.jev_monthly_budget_usd == 0.02
    assert service.settings.source_default_fetch_interval_seconds == 900
    assert service.executor.limits.deadline_seconds == 20
    assert service.executor.limits.max_parallel_calls == 1
    explicit = settings.model_copy(update={"jev_monthly_budget_usd": 4})
    assert (
        MarketAlertService(None, explicit, client=SimpleNamespace()).settings.jev_monthly_budget_usd
        == 4
    )


def test_article_correction_uses_observation_time_without_fabricating_fresh_publication(setup):
    service, _, _ = setup
    published = NOW - timedelta(hours=24)
    article = SimpleNamespace(
        id=1,
        url="https://example.com/story",
        title="ACME corrected results",
        extracted_text="Corrected loss",
        related_tickers=["ACME"],
        published_at=published,
        created_at=published,
        updated_at=NOW,
        revision=2,
        content_hash="new-hash",
        category="news",
        provider_metadata={},
    )
    source = SimpleNamespace(
        owner_user_id=None,
        enabled=True,
        trust_score=0.95,
        config={"primary_source": True},
        url="https://example.com/feed",
        category="news",
        name="Synthetic source",
        provider="rss",
    )
    result = service._evidence(article, source)
    assert result.revision == 2
    assert result.first_seen_at == NOW
    assert result.published_at == published


@pytest.mark.asyncio
async def test_ignored_unsupported_stale_and_low_trust_tones_do_not_enter_jev_series(setup):
    service, _, tracker = setup
    records = [
        evidence(
            article_id=str(index),
            url=f"https://source.example/{index}",
            trust_score=0.6 if index == 4 else 0.95,
        )
        for index in range(5)
    ]
    choices = ["daily_report", "ignore", "immediate_alert", "daily_report", "daily_report"]
    reasons = [
        "routine_update",
        "insufficient_evidence",
        "insufficient_evidence",
        "stale_or_duplicate",
        "routine_update",
    ]
    audited = []
    for index, record in enumerate(records):
        result = judgment(choices[index]).model_copy(
            update={
                "reason_code": reasons[index],
                "sentiments": {"ACME": "positive" if index == 0 else "negative"},
            }
        )
        audited.append(
            SimpleNamespace(
                status="completed",
                kind="shock",
                delivery_choice=choices[index],
                result={"judgment": result.model_dump(mode="json")},
                event_id=index,
                input_payload={"state": service._state([record])},
                completed_at=NOW,
            )
        )
    snapshots = []

    class FilterRepository:
        def __init__(self, session):
            pass

        async def list_recent_events(self, **kwargs):
            return []

        async def list_recent_decisions(self, **kwargs):
            return audited

        async def latest_sentiment(self, *args):
            return None

        async def save_sentiment(self, **kwargs):
            snapshots.append(kwargs)

    service.repository_type = FilterRepository
    service._articles = AsyncMock(return_value=records)
    await service._sentiments(NOW)
    assert len(snapshots) == 1
    current = snapshots[0]["windows"]["current"]
    assert current["counts"]["positive"] == 1
    assert current["counts"]["negative"] == 0
    assert current["story_count"] == 1
    # Raw linguistic classifications stay intact in the individual audit records.
    assert all(item.result["judgment"]["sentiments"]["ACME"] == "negative" for item in audited[1:])


def test_model_context_uses_current_revision_but_retains_original_audit(setup):
    service, _, _ = setup
    original = evidence(title="Obsolete initial claim", revision=1)
    corrected = evidence(title="Latest corrected claim", revision=2)
    audit = [original, corrected]
    state = service._state(audit)
    assert len(state["evidence"]) == 1
    assert state["evidence"][0]["title"] == corrected.title
    assert state["evidence"][0]["revision"] == 2
    assert audit == [original, corrected]
