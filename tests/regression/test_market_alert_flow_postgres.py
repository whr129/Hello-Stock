"""Isolated PostgreSQL full-flow checks; Jev and Telegram are always fake."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import delete, select
from test_alert_storage_postgres import URL, sessions  # noqa: F401

from news_agent.alerts.collector import FastNewsCollector
from news_agent.alerts.decision import JevDecision, JevError, JevUsage
from news_agent.alerts.delivery import MarketAlertDispatcher
from news_agent.alerts.service import MarketAlertService
from news_agent.alerts.storage import MarketAlertDecision, MarketAlertDelivery
from news_agent.ingestion.providers import NormalizedIngestItem
from news_agent.settings import Settings
from news_agent.storage.models import Article, Source

pytestmark = pytest.mark.skipif(not URL, reason="Dedicated PostgreSQL alerts test URL required")


@pytest_asyncio.fixture
async def flow(sessions):  # noqa: F811 - imported shared pytest fixture
    now = datetime.now(UTC)
    identity = uuid4().hex
    url = f"https://example.com/{identity}"
    settings = Settings(
        _env_file=None,
        openrouter_api_key="fictional-test-key",
        telegram_bot_token="000:fake-token",
        market_alerts_enabled=True,
        market_alert_shadow_mode=False,
        market_alert_chat_id=123,
        default_source_pack_enabled=False,
        market_universe_symbols="NVDA",
        market_alert_quiet_start="",
        market_alert_quiet_end="",
    )
    async with sessions.begin() as session:
        source = Source(
            name="Synthetic IR",
            url=url,
            external_account=url,
            provider="rss",
            owner_user_id=None,
            category="company_ir",
            enabled=True,
            trust_score=0.95,
            config={"alert_enabled": True, "primary_source": True},
            field_mapping={},
        )
        session.add(source)
        await session.flush()
        source_id = source.id

    class Provider:
        def fetch_items(self, source, timeout_seconds):
            if source.id != source_id:
                return []
            return [
                NormalizedIngestItem(
                    identity,
                    url + "/story",
                    "Nvidia cuts annual revenue guidance after factory closure",
                    "Synthetic test: NVDA reduced its revenue forecast after a factory closure.",
                    now,
                    None,
                    {},
                    "rss",
                    url,
                    {"tickers": ["NVDA"]},
                )
            ]

    registry = SimpleNamespace(get=lambda provider: Provider())
    judgment = JevDecision(
        delivery="immediate_alert",
        confidence=0.98,
        materiality=2.0,
        event_type="guidance",
        reason_code="material_new_event",
        sentiments={"NVDA": "negative"},
        model="typesafe/jev-1.14",
        usage=JevUsage(input_tokens=100, output_tokens=0, cost=0.00001),
        probabilities={},
        attempts=1,
    )
    client = SimpleNamespace(assess=AsyncMock(return_value=judgment))
    service = MarketAlertService(sessions, settings, client, started_at=now)
    sent = []

    class Bot:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send_message(self, **kwargs):
            sent.append(kwargs)
            return SimpleNamespace(message_id=len(sent))

    collector = FastNewsCollector(sessions, settings, registry)
    dispatcher = MarketAlertDispatcher(sessions, settings, bot_factory=Bot)
    try:
        result = await collector.collect_once(now)
        assert result["saved"] == 1
        yield SimpleNamespace(
            sessions=sessions,
            settings=settings,
            client=client,
            service=service,
            dispatcher=dispatcher,
            sent=sent,
            judgment=judgment,
            now=now,
            collector=collector,
            url=url,
        )
    finally:
        await service.aclose()
        async with sessions.begin() as session:
            await session.execute(delete(Article).where(Article.source_id == source_id))
            await session.execute(delete(Source).where(Source.id == source_id))


@pytest.mark.asyncio
async def test_fresh_ingestion_decision_delivery_daily_context_and_poll_dedup(flow):
    first = await flow.service.process_once()
    assert first["queued"] == 1
    assert flow.client.assess.await_count == 1
    await flow.service.process_once()
    assert flow.client.assess.await_count == 1
    assert await flow.dispatcher.dispatch_once() == 1
    assert await flow.dispatcher.dispatch_once() == 0
    assert len(flow.sent) == 1
    assert flow.sent[0]["chat_id"] == 123
    assert flow.url + "/story" in flow.sent[0]["text"]
    context = await flow.service.report_context(datetime.now(UTC), ticker="NVDA")
    assert flow.url + "/story" in context
    async with flow.sessions() as session:
        decisions = (await session.scalars(select(MarketAlertDecision))).all()
        deliveries = (await session.scalars(select(MarketAlertDelivery))).all()
        assert len(decisions) == len(deliveries) == 1
        assert decisions[0].status == "completed"
        assert deliveries[0].status == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["daily_report", "ignore"])
async def test_nonurgent_jev_routes_do_not_send(flow, choice):
    flow.client.assess.return_value = flow.judgment.model_copy(update={"delivery": choice})
    await flow.service.process_once()
    assert await flow.dispatcher.dispatch_once() == 0
    assert not flow.sent
    async with flow.sessions() as session:
        decision = (await session.scalars(select(MarketAlertDecision))).one()
        assert decision.delivery_choice == choice
        assert not (await session.scalars(select(MarketAlertDelivery))).all()
    context = await flow.service.report_context(datetime.now(UTC))
    assert (flow.url + "/story" in context) == (choice == "daily_report")


@pytest.mark.asyncio
async def test_shadow_persists_decision_without_outbox(flow):
    flow.service.policy = flow.service.policy.model_copy(update={"shadow_mode": True})
    await flow.service.process_once()
    async with flow.sessions() as session:
        decision = (await session.scalars(select(MarketAlertDecision))).one()
        assert decision.status == "completed"
        assert decision.result["guard"]["reason"] == "shadow_mode"
        assert not (await session.scalars(select(MarketAlertDelivery))).all()
    assert await flow.dispatcher.dispatch_once() == 0


@pytest.mark.asyncio
async def test_auth_failure_stops_retries_and_rotation_preserves_audit(flow):
    flow.client.assess.side_effect = JevError("authentication", status_code=401)
    await flow.service.process_once()
    await flow.service.process_once()
    assert flow.client.assess.await_count == 1
    rotated_settings = flow.settings.model_copy(
        update={"openrouter_api_key": SecretStr("rotated-fake")}
    )
    replacement = SimpleNamespace(assess=AsyncMock(return_value=flow.judgment))
    restarted = MarketAlertService(
        flow.sessions,
        rotated_settings,
        replacement,
        started_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    await restarted.process_once()
    assert replacement.assess.await_count == 1
    async with flow.sessions() as session:
        decisions = (await session.scalars(select(MarketAlertDecision))).all()
        assert sorted(row.status for row in decisions) == ["blocked", "completed"]
        assert len({row.model_revision for row in decisions}) == 2
        assert (
            next(row for row in decisions if row.status == "blocked").last_error == "authentication"
        )


@pytest.mark.asyncio
async def test_monthly_budget_exhaustion_prevents_provider_request(flow):
    flow.service.settings = flow.settings.model_copy(update={"jev_monthly_budget_usd": 0})
    result = await flow.service.process_once()
    assert result["budget_exhausted"] == 1
    flow.client.assess.assert_not_awaited()
    assert await flow.dispatcher.dispatch_once() == 0


@pytest.mark.asyncio
async def test_existing_daily_destination_is_used_without_alert_override(flow):
    settings = flow.settings.model_copy(
        update={"market_alert_chat_id": 0, "daily_research_report_chat_id": 456}
    )
    service = MarketAlertService(flow.sessions, settings, flow.client, started_at=flow.now)
    await service.process_once()
    assert service.policy.chat_id == 456
    async with flow.sessions() as session:
        delivery = (await session.scalars(select(MarketAlertDelivery))).one()
        assert delivery.destination == 456
    dispatcher = MarketAlertDispatcher(
        flow.sessions, settings, bot_factory=flow.dispatcher.bot_factory
    )
    assert await dispatcher.dispatch_once() == 1
    assert flow.sent[0]["chat_id"] == 456


@pytest.mark.asyncio
async def test_exhausted_and_delayed_backlog_cannot_starve_new_decisions(flow):
    from news_agent.alerts.storage import AlertRepository

    now = datetime.now(UTC)
    async with flow.sessions.begin() as session:
        event = await AlertRepository(session).register_event(
            "unrelated-backlog",
            scope="BACKLOG",
            event_type="other",
            tickers=[],
            evidence=[],
            payload={},
            revision_key="one",
            now=now,
        )
        for index in range(120):
            session.add(
                MarketAlertDecision(
                    event_id=event.id,
                    event_revision=1,
                    input_digest=f"backlog-{index}",
                    kind="shock",
                    model_revision=flow.service.revision,
                    input_payload={},
                    result={},
                    status="failed",
                    attempts=3 if index < 60 else 1,
                    next_attempt_at=None if index < 60 else now + timedelta(hours=1),
                    created_at=now - timedelta(minutes=2),
                )
            )
    result = await flow.service.process_once()
    assert result["queued"] == 1
    assert flow.client.assess.await_count == 1


@pytest.mark.asyncio
async def test_report_context_keeps_routed_events_and_latest_distinct_sentiment_series(flow):
    from news_agent.alerts.sentiment import aggregate_windows
    from news_agent.alerts.storage import MarketSentimentSnapshot

    flow.client.assess.return_value = flow.judgment.model_copy(update={"delivery": "daily_report"})
    await flow.service.process_once()
    now = datetime.now(UTC)
    async with flow.sessions.begin() as session:
        routed = (await session.scalars(select(MarketAlertDecision))).one()
        routed.created_at = now - timedelta(minutes=5)
        for index in range(110):
            session.add(
                MarketAlertDecision(
                    event_id=routed.event_id,
                    event_revision=1,
                    input_digest=f"ignored-{index}",
                    kind="shock",
                    model_revision="test-ignored",
                    input_payload={},
                    result={},
                    status="completed",
                    delivery_choice="ignore",
                    attempts=1,
                    created_at=now - timedelta(minutes=1),
                )
            )
            stamp = now - timedelta(seconds=index)
            windows = aggregate_windows(
                [], ticker="NVDA", provider="synthetic", model_version="test", as_of=stamp
            )
            session.add(
                MarketSentimentSnapshot(
                    ticker="NVDA",
                    provider="synthetic",
                    model_revision="test",
                    as_of=stamp,
                    watermark=windows.watermark,
                    windows=windows.model_dump(mode="json"),
                    state={},
                    gaps=["insufficient_coverage"],
                )
            )
        older = now - timedelta(minutes=3)
        windows = aggregate_windows(
            [], ticker="OTHER", provider="synthetic", model_version="test", as_of=older
        )
        session.add(
            MarketSentimentSnapshot(
                ticker="OTHER",
                provider="synthetic",
                model_revision="test",
                as_of=older,
                watermark=windows.watermark,
                windows=windows.model_dump(mode="json"),
                state={},
                gaps=["insufficient_coverage"],
            )
        )
    context = await flow.service.report_context(now)
    assert flow.url + "/story" in context
    assert "OTHER synthetic/test" in context
    assert context.count("NVDA synthetic/test") == 1


@pytest.mark.asyncio
async def test_same_url_content_correction_reaches_jev_and_bypasses_only_event_cooldown(flow):
    from news_agent.alerts.storage import MarketAlertEvent
    from news_agent.storage.repositories import ArticleRepository

    await flow.service.process_once()
    assert await flow.dispatcher.dispatch_once() == 1
    restarted_at = datetime.now(UTC)
    async with flow.sessions.begin() as session:
        original = (
            await session.scalars(select(Article).where(Article.url == flow.url + "/story"))
        ).one()
        published = original.published_at
        old_title = original.title
        changed, saved = await ArticleRepository(session).upsert_article(
            source_id=original.source_id,
            url=original.url,
            title="Correction: Nvidia cuts annual revenue guidance by 80% after factory closure",
            extracted_text=(
                "The issuer corrects the earlier 20% reduction to an 80% revenue cut; "
                "this is a substantive new disclosure."
            ),
            content_hash="corrected-hash",
            category=original.category,
            published_at=published,
            related_tickers=["NVDA"],
            provider_metadata=original.provider_metadata,
        )
        assert saved and changed.id == original.id and changed.revision == 2
        assert changed.published_at == published
        assert changed.created_at < restarted_at <= changed.updated_at
    replacement = SimpleNamespace(assess=AsyncMock(return_value=flow.judgment))
    service = MarketAlertService(flow.sessions, flow.settings, replacement, started_at=restarted_at)
    outcome = await service.process_once()
    assert outcome["queued"] == 1
    assert replacement.assess.await_count == 1
    state = replacement.assess.call_args.args[0]
    assert state["event_revision"] == 2
    assert state["delivery_history"][0]["status"] == "delivered"
    assert await flow.dispatcher.dispatch_once() == 1
    assert len(flow.sent) == 2
    corrected_title = "Correction: Nvidia cuts annual revenue guidance by 80% after factory closure"
    assert corrected_title in flow.sent[1]["text"]
    assert old_title not in flow.sent[1]["text"]
    assert len(state["evidence"]) == 1 and state["evidence"][0]["revision"] == 2
    context = await service.report_context(datetime.now(UTC))
    assert corrected_title in context and old_title not in context
    await service.process_once()
    assert replacement.assess.await_count == 1
    async with flow.sessions() as session:
        events = (await session.scalars(select(MarketAlertEvent))).all()
        assert len(events) == 1 and events[0].revision == 2
        assert {item["revision"] for item in events[0].evidence} == {1, 2}
        decisions = (await session.scalars(select(MarketAlertDecision))).all()
        assert len(decisions) == 2
        assert all(item.result["guard"]["reason"] == "allowed" for item in decisions)


@pytest.mark.asyncio
async def test_ignored_fabricated_negative_tone_stays_audited_but_not_in_aggregate_or_report(flow):
    from news_agent.alerts.storage import MarketSentimentSnapshot

    flow.client.assess.return_value = flow.judgment.model_copy(
        update={"delivery": "ignore", "reason_code": "insufficient_evidence"}
    )
    await flow.service.process_once()
    await flow.service.process_once()
    async with flow.sessions() as session:
        decision = (await session.scalars(select(MarketAlertDecision))).one()
        assert decision.result["judgment"]["sentiments"] == {"NVDA": "negative"}
        assert not (
            await session.scalars(
                select(MarketSentimentSnapshot).where(MarketSentimentSnapshot.provider == "jev")
            )
        ).all()
    assert flow.url + "/story" not in await flow.service.report_context(datetime.now(UTC))
    assert await flow.dispatcher.dispatch_once() == 0


@pytest.mark.asyncio
async def test_queued_original_expires_after_same_url_retraction_ignored_by_jev(flow):
    from news_agent.alerts.storage import MarketAlertEvent
    from news_agent.storage.repositories import ArticleRepository

    await flow.service.process_once()
    assert not flow.sent
    async with flow.sessions.begin() as session:
        article = (
            await session.scalars(select(Article).where(Article.url == flow.url + "/story"))
        ).one()
        original_title = article.title
        await ArticleRepository(session).upsert_article(
            source_id=article.source_id,
            url=article.url,
            title="Retraction: Nvidia guidance-cut announcement was published in error",
            extracted_text=(
                "The issuer withdraws the guidance-cut announcement as erroneous. "
                "No actual guidance change occurred."
            ),
            content_hash="retracted-hash",
            category=article.category,
            published_at=article.published_at,
            related_tickers=["NVDA"],
            provider_metadata=article.provider_metadata,
        )
    flow.client.assess.return_value = flow.judgment.model_copy(
        update={"delivery": "ignore", "reason_code": "insufficient_evidence"}
    )
    await flow.service.process_once()
    assert flow.client.assess.await_count == 2
    assert await flow.dispatcher.dispatch_once() == 0
    assert not flow.sent
    context = await flow.service.report_context(datetime.now(UTC))
    assert original_title not in context
    async with flow.sessions() as session:
        deliveries = (await session.scalars(select(MarketAlertDelivery))).all()
        assert len(deliveries) == 1 and deliveries[0].status == "expired"
        decisions = (await session.scalars(select(MarketAlertDecision))).all()
        assert len(decisions) == 2
        assert {row.delivery_choice for row in decisions} == {"ignore", "immediate_alert"}
        events = (await session.scalars(select(MarketAlertEvent))).all()
        assert len(events) == 1
        assert {row["revision"] for row in events[0].evidence} == {1, 2}
        from news_agent.alerts.storage import MarketSentimentSnapshot

        snapshots = (
            await session.scalars(
                select(MarketSentimentSnapshot)
                .where(MarketSentimentSnapshot.provider == "jev")
                .order_by(MarketSentimentSnapshot.as_of.desc())
            )
        ).all()
        assert snapshots and snapshots[0].windows["current"]["story_count"] == 0
