import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from news_agent.alerts.storage import (
    AlertRepository,
    DeliveryRequest,
    MarketAlertBudget,
    MarketAlertDecision,
    MarketAlertDelivery,
    MarketAlertEvent,
    MarketSentimentSnapshot,
    StaleClaimError,
)

NOW = datetime(2026, 10, 30, 18, tzinfo=UTC)
TOKEN = "owned-lease"


def result(item=None, *, items=(), rowcount=0):
    return Mock(
        scalar_one=Mock(return_value=item),
        scalar_one_or_none=Mock(return_value=item),
        scalars=Mock(return_value=list(items)),
        rowcount=rowcount,
    )


def session_for(*results):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=list(results)),
        flush=AsyncMock(),
        commit=AsyncMock(),
        get=AsyncMock(),
    )


def decision(**overrides):
    values = {
        "id": 7,
        "event_id": 2,
        "event_revision": 1,
        "status": "assessing",
        "attempts": 1,
        "lease_token": TOKEN,
        "lease_expires_at": NOW + timedelta(minutes=2),
        "reserved_usd": Decimal("0"),
        "cost_usd": Decimal("0"),
        "budget_month": None,
    }
    return MarketAlertDecision(**(values | overrides))


def delivery(**overrides):
    values = {
        "id": 9,
        "event_id": 2,
        "decision_id": 7,
        "revision": 1,
        "status": "sending",
        "attempts": 1,
        "lease_token": TOKEN,
        "lease_expires_at": NOW + timedelta(minutes=2),
        "chunks": ["first", "second"],
        "chunks_sent": 0,
        "message_ids": [],
    }
    return MarketAlertDelivery(**(values | overrides))


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_reservation_counts_spend_and_inflight_across_workers() -> None:
    item = decision()
    budget = MarketAlertBudget(
        month="2026-10", spent_usd=Decimal("4.97"), reserved_usd=Decimal("0.02")
    )
    session = session_for(result(item), result(), result(budget))

    allowed = await AlertRepository(session).reserve_budget(
        item.id, TOKEN, amount_usd="0.025", monthly_limit_usd="5", now=NOW
    )

    assert allowed is False
    assert item.reserved_usd == 0
    assert budget.reserved_usd == Decimal("0.02")
    assert "FOR UPDATE" in sql(session.execute.call_args_list[0].args[0])
    assert "FOR UPDATE" in sql(session.execute.call_args_list[2].args[0])
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reservation_is_idempotent_for_same_owned_attempt() -> None:
    item = decision()
    budget = MarketAlertBudget(month="2026-10", spent_usd=Decimal("1"), reserved_usd=Decimal("0"))
    session = session_for(result(item), result(), result(budget), result(item))
    repository = AlertRepository(session)

    for _ in range(2):
        assert await repository.reserve_budget(
            item.id, TOKEN, amount_usd="0.025", monthly_limit_usd="5", now=NOW
        )

    assert budget.reserved_usd == Decimal("0.025")
    assert item.reserved_usd == Decimal("0.025")
    assert item.budget_month == "2026-10"


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", "0"])
async def test_invalid_reservation_amount_never_touches_database(amount) -> None:
    session = session_for()
    with pytest.raises(ValueError):
        await AlertRepository(session).reserve_budget(
            7, TOKEN, amount_usd=amount, monthly_limit_usd="5", now=NOW
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_decision_settles_budget_and_queues_frozen_delivery_atomically() -> None:
    item = decision(reserved_usd=Decimal("0.025"), budget_month="2026-10")
    budget = MarketAlertBudget(
        month="2026-10", spent_usd=Decimal("1"), reserved_usd=Decimal("0.025")
    )
    session = session_for(result(item), result(), result(budget), result())

    saved = await AlertRepository(session).complete_decision(
        item.id,
        TOKEN,
        result={"evidence": ["https://example.test/disclosure"]},
        delivery_choice="immediate_alert",
        actual_cost_usd="0.000084",
        now=NOW,
        delivery=DeliveryRequest(
            destination=-123,
            kind="shock",
            message_text="first\nsecond",
            expires_at=NOW + timedelta(minutes=30),
            chunks=("first", "second"),
        ),
    )

    assert saved.status == "completed"
    assert saved.cost_usd == Decimal("0.000084")
    assert budget.spent_usd == Decimal("1.000084")
    assert budget.reserved_usd == saved.reserved_usd == 0
    assert saved.lease_token is None
    insert_statement = session.execute.call_args_list[-1].args[0]
    assert "ON CONFLICT ON CONSTRAINT uq_alert_delivery_event DO NOTHING" in sql(insert_statement)
    values = insert_statement.compile(dialect=postgresql.dialect()).params
    assert values["chunks"] == ["first", "second"]
    assert values["message_text"] == "first\nsecond"
    assert values["destination"] == -123
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides", [{"lease_token": "another-worker"}, {"lease_expires_at": NOW}]
)
async def test_stale_decision_cannot_settle_or_queue(overrides) -> None:
    session = session_for(result(decision(**overrides)))
    with pytest.raises(StaleClaimError):
        await AlertRepository(session).complete_decision(
            7,
            TOKEN,
            result={},
            delivery_choice="immediate_alert",
            actual_cost_usd=0,
            now=NOW,
        )
    assert session.execute.await_count == 1
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_or_ignore_decision_cannot_queue_immediate_delivery() -> None:
    session = session_for()
    with pytest.raises(ValueError, match="Only immediate"):
        await AlertRepository(session).complete_decision(
            7,
            TOKEN,
            result={},
            delivery_choice="daily_report",
            actual_cost_usd=0,
            now=NOW,
            delivery=DeliveryRequest(123, "shock", "text", NOW + timedelta(hours=1)),
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_claim_excludes_unknown_sent_and_expired_records() -> None:
    item = delivery(status="pending", attempts=0, lease_token=None)
    session = session_for(result(item))

    claimed = await AlertRepository(session).claim_delivery(
        now=NOW, lease_seconds=120, max_attempts=3
    )

    assert claimed is item
    assert item.status == "sending"
    assert item.attempts == 1
    assert item.lease_token
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "FOR UPDATE SKIP LOCKED" in str(compiled)
    assert "market_alert_deliveries.expires_at >" in str(compiled)
    assert ["pending", "failed"] in compiled.params.values()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_chunk_checkpoints_preserve_order_and_stop_conflicting_replays() -> None:
    item = delivery()
    session = session_for(*(result(item) for _ in range(7)))
    repository = AlertRepository(session)
    await repository.checkpoint_chunk(9, TOKEN, 0, 101, now=NOW)
    await repository.checkpoint_chunk(9, TOKEN, 0, 101, now=NOW)
    assert item.chunks_sent == 1
    assert item.message_ids == [101]
    with pytest.raises(ValueError, match="conflicts"):
        await repository.checkpoint_chunk(9, TOKEN, 0, 999, now=NOW)
    with pytest.raises(ValueError, match="in order"):
        await repository.checkpoint_chunk(9, TOKEN, 2, 103, now=NOW)
    with pytest.raises(ValueError, match="every chunk"):
        await repository.finish_delivery(9, TOKEN, status="delivered", now=NOW)
    await repository.checkpoint_chunk(9, TOKEN, 1, 102, now=NOW)
    await repository.finish_delivery(9, TOKEN, status="delivered", now=NOW)
    assert item.message_ids == [101, 102]
    assert item.status == "delivered"
    assert item.delivered_at == NOW


@pytest.mark.asyncio
async def test_expired_workers_consume_reservation_and_never_replay_uncertain_delivery() -> None:
    item = decision(reserved_usd=Decimal("0.025"), budget_month="2026-10", lease_expires_at=NOW)
    budget = MarketAlertBudget(
        month="2026-10", spent_usd=Decimal("0"), reserved_usd=Decimal("0.025")
    )
    session = session_for(
        result(items=[item]), result(), result(budget), result(rowcount=1), result(rowcount=2)
    )

    counts = await AlertRepository(session).recover_expired(now=NOW)

    assert counts == {"decisions": 1, "unknown": 1, "expired": 2}
    assert item.status == "failed"
    assert item.cost_usd == budget.spent_usd == Decimal("0.025")
    assert budget.reserved_usd == 0
    assert item.lease_token is None
    unknown_update = session.execute.call_args_list[-2].args[0]
    params = unknown_update.compile(dialect=postgresql.dialect()).params
    assert params["status"] == "unknown"
    assert params["status_1"] == "sending"
    assert params["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_known_old_event_revision_does_not_roll_back_latest_material_facts() -> None:
    event = MarketAlertEvent(
        id=2,
        event_key="issuer:event",
        revision=2,
        revision_key="corrected",
        revision_history=["original", "corrected"],
        payload={"fact": "corrected"},
        tickers=["ABC"],
        aliases=[],
        evidence=[{"url": "https://example.test/correction"}],
    )
    session = session_for(result(), result(event))

    saved = await AlertRepository(session).register_event(
        "issuer:event",
        scope="ABC",
        event_type="guidance",
        tickers=["ABC"],
        evidence=[{"url": "https://example.test/original"}],
        payload={"fact": "old"},
        revision_key="original",
        now=NOW,
        aliases=["other-provider-id"],
    )

    assert saved.revision == 2
    assert saved.revision_key == "corrected"
    assert saved.payload == {"fact": "corrected"}
    assert saved.aliases == ["other-provider-id"]
    assert len(saved.evidence) == 2


def test_alert_schema_keeps_retained_evidence_independent_and_deduplicates_delivery() -> None:
    models = [
        MarketAlertEvent,
        MarketAlertDecision,
        MarketAlertDelivery,
        MarketAlertBudget,
        MarketSentimentSnapshot,
    ]
    for model in models:
        ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert "runtime_runs" not in ddl
        assert "REFERENCES articles" not in ddl
    delivery_keys = {
        tuple(column.name for column in constraint.columns)
        for constraint in MarketAlertDelivery.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("decision_id", "revision", "kind", "destination") in delivery_keys
    assert ("event_id", "revision", "kind", "destination") in delivery_keys


def test_frozen_migration_matches_alert_model_columns_and_preserves_article_history() -> None:
    spec = importlib.util.spec_from_file_location(
        "market_alert_migration", Path("migrations/versions/0007_market_alerts.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = Mock()
    module.op = operations

    module.upgrade()

    assert module.down_revision == "0006_market_research_only_cleanup"
    created = {
        call.args[0]: {item.name for item in call.args[1:] if hasattr(item, "type")}
        for call in operations.create_table.call_args_list
    }
    for model in (
        MarketAlertEvent,
        MarketAlertDecision,
        MarketAlertDelivery,
        MarketAlertBudget,
        MarketSentimentSnapshot,
    ):
        assert created[model.__tablename__] == set(model.__table__.columns.keys())
    assert operations.add_column.call_args.args[0] == "articles"
    assert operations.add_column.call_args.args[1].name == "provider_metadata"
    operations.drop_table.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_delivery_does_not_settle_or_complete_decision() -> None:
    session = session_for()
    with pytest.raises(ValueError, match="expired"):
        await AlertRepository(session).complete_decision(
            7,
            TOKEN,
            result={},
            delivery_choice="immediate_alert",
            actual_cost_usd=0,
            now=NOW,
            delivery=DeliveryRequest(123, "news", "message", NOW),
        )
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()
