"""Durable alert state. Callers commit before doing any network work."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from news_agent.storage.models import Base

MONEY = Numeric(20, 12)
ZERO = Decimal("0")


class MarketAlertEvent(Base):
    __tablename__ = "market_alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(255), unique=True)
    scope: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    tickers: Mapped[list[str]] = mapped_column(JSONB, default=list)
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    revision_key: Mapped[str] = mapped_column(String(128))
    revision_history: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketAlertDecision(Base):
    __tablename__ = "market_alert_decisions"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "input_digest", "kind", "model_revision", name="uq_alert_decision_input"
        ),
        CheckConstraint("reserved_usd >= 0 AND cost_usd >= 0", name="ck_alert_decision_cost"),
        CheckConstraint(
            "status IN ('pending','assessing','completed','failed','blocked')",
            name="ck_alert_decision_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("market_alert_events.id"), index=True)
    event_revision: Mapped[int] = mapped_column(Integer)
    input_digest: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    model_revision: Mapped[str] = mapped_column(String(255))
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    delivery_choice: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    reserved_usd: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    cost_usd: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketAlertDelivery(Base):
    __tablename__ = "market_alert_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "decision_id", "revision", "kind", "destination", name="uq_alert_delivery_decision"
        ),
        UniqueConstraint(
            "event_id", "revision", "kind", "destination", name="uq_alert_delivery_event"
        ),
        CheckConstraint(
            "status IN ('pending','sending','delivered','failed','unknown','expired')",
            name="ck_alert_delivery_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("market_alert_events.id"), index=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("market_alert_decisions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    destination: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    message_text: Mapped[str] = mapped_column(Text)
    chunks: Mapped[list[str]] = mapped_column(JSONB, default=list)
    chunks_sent: Mapped[int] = mapped_column(Integer, default=0)
    message_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketAlertBudget(Base):
    __tablename__ = "market_alert_budgets"
    __table_args__ = (
        CheckConstraint("spent_usd >= 0 AND reserved_usd >= 0", name="ck_alert_budget_amounts"),
    )

    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    spent_usd: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    reserved_usd: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketSentimentSnapshot(Base):
    __tablename__ = "market_sentiment_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "provider", "model_revision", "as_of", name="uq_sentiment_snapshot_window"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model_revision: Mapped[str] = mapped_column(String(255))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    watermark: Mapped[str] = mapped_column(String(128))
    windows: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    gaps: Mapped[list[str]] = mapped_column(JSONB, default=list)


@dataclass(frozen=True)
class DeliveryRequest:
    destination: int
    kind: str
    message_text: str
    expires_at: datetime
    chunks: tuple[str, ...] = ()
    not_before: datetime | None = None


class StaleClaimError(RuntimeError):
    """A worker lost ownership; its result must not cause a side effect."""


def _amount(value: Decimal | float | str) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < ZERO:
        raise ValueError("Cost must be finite and nonnegative")
    return amount.quantize(Decimal("0.000000000001"))


def _assert_claim(item: Any, token: str, now: datetime, status: str) -> None:
    if (
        item is None
        or item.status != status
        or item.lease_token != token
        or item.lease_expires_at is None
        or item.lease_expires_at <= now
    ):
        raise StaleClaimError("Alert claim is no longer owned by this worker")


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register_event(
        self,
        event_key: str,
        *,
        scope: str,
        event_type: str,
        tickers: Sequence[str],
        evidence: list[dict[str, Any]],
        payload: dict[str, Any],
        revision_key: str,
        now: datetime,
        aliases: Sequence[str] = (),
    ) -> MarketAlertEvent:
        await self.session.execute(
            insert(MarketAlertEvent)
            .values(
                event_key=event_key,
                scope=scope,
                event_type=event_type,
                tickers=list(tickers),
                aliases=list(aliases),
                evidence=evidence,
                payload=payload,
                revision=1,
                revision_key=revision_key,
                revision_history=[revision_key],
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["event_key"])
        )
        item = (
            await self.session.execute(
                select(MarketAlertEvent)
                .where(MarketAlertEvent.event_key == event_key)
                .with_for_update()
            )
        ).scalar_one()
        history = list(item.revision_history or [item.revision_key])
        if revision_key not in history:
            item.revision += 1
            item.revision_key = revision_key
            item.revision_history = [*history, revision_key]
            item.payload = dict(payload)
            item.updated_at = now
        item.tickers = sorted(set(item.tickers or []) | set(tickers))
        item.aliases = sorted(set(item.aliases or []) | set(aliases))
        merged = list(item.evidence or [])
        merged.extend(entry for entry in evidence if entry not in merged)
        item.evidence = merged
        await self.session.flush()
        return item

    async def prepare_decision(
        self,
        event_id: int,
        *,
        input_digest: str,
        kind: str,
        model_revision: str,
        input_payload: dict[str, Any],
        now: datetime,
    ) -> MarketAlertDecision:
        event = await self.session.get(MarketAlertEvent, event_id)
        if event is None:
            raise ValueError("Unknown market event")
        await self.session.execute(
            insert(MarketAlertDecision)
            .values(
                event_id=event_id,
                event_revision=event.revision,
                input_digest=input_digest,
                kind=kind,
                model_revision=model_revision,
                input_payload=input_payload,
                result={},
                status="pending",
                attempts=0,
                reserved_usd=ZERO,
                cost_usd=ZERO,
                created_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_alert_decision_input")
        )
        return (
            await self.session.execute(
                select(MarketAlertDecision).where(
                    MarketAlertDecision.event_id == event_id,
                    MarketAlertDecision.input_digest == input_digest,
                    MarketAlertDecision.kind == kind,
                    MarketAlertDecision.model_revision == model_revision,
                )
            )
        ).scalar_one()

    async def claim_decision(
        self, decision_id: int, *, now: datetime, lease_seconds: int, max_attempts: int
    ) -> MarketAlertDecision | None:
        item = (
            await self.session.execute(
                select(MarketAlertDecision)
                .where(
                    MarketAlertDecision.id == decision_id,
                    MarketAlertDecision.status.in_(("pending", "failed")),
                    MarketAlertDecision.attempts < max_attempts,
                    or_(
                        MarketAlertDecision.next_attempt_at.is_(None),
                        MarketAlertDecision.next_attempt_at <= now,
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if item is None:
            return None
        item.status = "assessing"
        item.attempts += 1
        item.lease_token = str(uuid4())
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self.session.flush()
        return item

    async def _decision(self, decision_id: int) -> MarketAlertDecision | None:
        return (
            await self.session.execute(
                select(MarketAlertDecision)
                .where(MarketAlertDecision.id == decision_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _budget(self, month: str, now: datetime) -> MarketAlertBudget:
        await self.session.execute(
            insert(MarketAlertBudget)
            .values(month=month, spent_usd=ZERO, reserved_usd=ZERO, updated_at=now)
            .on_conflict_do_nothing(index_elements=["month"])
        )
        return (
            await self.session.execute(
                select(MarketAlertBudget).where(MarketAlertBudget.month == month).with_for_update()
            )
        ).scalar_one()

    async def reserve_budget(
        self,
        decision_id: int,
        lease_token: str,
        *,
        amount_usd: Decimal | float | str,
        monthly_limit_usd: Decimal | float | str,
        now: datetime,
    ) -> bool:
        amount, limit = _amount(amount_usd), _amount(monthly_limit_usd)
        if amount <= ZERO:
            raise ValueError("A positive reservation is required")
        item = await self._decision(decision_id)
        _assert_claim(item, lease_token, now, "assessing")
        if item.reserved_usd > ZERO:
            return item.reserved_usd == amount
        month = now.astimezone(UTC).strftime("%Y-%m")
        budget = await self._budget(month, now)
        if budget.spent_usd + budget.reserved_usd + amount > limit:
            return False
        budget.reserved_usd += amount
        budget.updated_at = now
        item.budget_month = month
        item.reserved_usd = amount
        await self.session.flush()
        return True

    async def _settle(
        self,
        item: MarketAlertDecision,
        actual_cost_usd: Decimal | float | str | None,
        now: datetime,
    ) -> None:
        cost = item.reserved_usd if actual_cost_usd is None else _amount(actual_cost_usd)
        if item.budget_month is not None:
            budget = await self._budget(item.budget_month, now)
            budget.reserved_usd -= item.reserved_usd
            budget.spent_usd += cost
            budget.updated_at = now
        elif cost > ZERO:
            raise ValueError("Cannot settle an unreserved paid request")
        item.cost_usd += cost
        item.reserved_usd = ZERO

    async def complete_decision(
        self,
        decision_id: int,
        lease_token: str,
        *,
        result: dict[str, Any],
        delivery_choice: str,
        actual_cost_usd: Decimal | float | str | None,
        now: datetime,
        delivery: DeliveryRequest | None = None,
    ) -> MarketAlertDecision:
        if delivery_choice not in {"immediate_alert", "daily_report", "ignore"}:
            raise ValueError("Invalid delivery choice")
        if delivery is not None and delivery_choice != "immediate_alert":
            raise ValueError("Only immediate decisions can queue an alert")
        chunks: list[str] = []
        if delivery is not None:
            if not delivery.destination or not delivery.message_text.strip():
                raise ValueError("A destination and alert text are required")
            if delivery.expires_at <= now:
                raise ValueError("Cannot queue an expired alert")
            chunks = list(delivery.chunks or (delivery.message_text,))
            if not all(isinstance(chunk, str) and chunk for chunk in chunks):
                raise ValueError("Alert chunks must be nonempty text")
        item = await self._decision(decision_id)
        _assert_claim(item, lease_token, now, "assessing")
        await self._settle(item, actual_cost_usd, now)
        item.result = dict(result)
        item.delivery_choice = delivery_choice
        item.status = "completed"
        item.completed_at = now
        item.lease_token = None
        item.lease_expires_at = None
        item.last_error = None
        if delivery is not None:
            await self.session.execute(
                insert(MarketAlertDelivery)
                .values(
                    event_id=item.event_id,
                    decision_id=item.id,
                    revision=item.event_revision,
                    kind=delivery.kind,
                    destination=delivery.destination,
                    status="pending",
                    message_text=delivery.message_text,
                    chunks=chunks,
                    chunks_sent=0,
                    message_ids=[],
                    attempts=0,
                    next_attempt_at=delivery.not_before,
                    expires_at=delivery.expires_at,
                    created_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_alert_delivery_event")
            )
        await self.session.flush()
        return item

    async def fail_decision(
        self,
        decision_id: int,
        lease_token: str,
        *,
        error: str,
        retry_at: datetime | None,
        now: datetime,
        actual_cost_usd: Decimal | float | str | None = None,
        status: str = "failed",
    ) -> MarketAlertDecision:
        if status not in {"failed", "blocked"}:
            raise ValueError("Invalid failed decision status")
        item = await self._decision(decision_id)
        _assert_claim(item, lease_token, now, "assessing")
        await self._settle(item, actual_cost_usd, now)
        item.status = status
        item.last_error = error
        item.next_attempt_at = retry_at
        item.lease_token = None
        item.lease_expires_at = None
        await self.session.flush()
        return item

    async def claim_delivery(
        self, *, now: datetime, lease_seconds: int, max_attempts: int
    ) -> MarketAlertDelivery | None:
        item = (
            await self.session.execute(
                select(MarketAlertDelivery)
                .where(
                    MarketAlertDelivery.status.in_(("pending", "failed")),
                    MarketAlertDelivery.attempts < max_attempts,
                    MarketAlertDelivery.expires_at > now,
                    or_(
                        MarketAlertDelivery.next_attempt_at.is_(None),
                        MarketAlertDelivery.next_attempt_at <= now,
                    ),
                )
                .order_by(MarketAlertDelivery.created_at, MarketAlertDelivery.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if item is None:
            return None
        item.status = "sending"
        item.attempts += 1
        item.lease_token = str(uuid4())
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self.session.flush()
        return item

    async def _delivery(self, delivery_id: int) -> MarketAlertDelivery | None:
        return (
            await self.session.execute(
                select(MarketAlertDelivery)
                .where(MarketAlertDelivery.id == delivery_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def checkpoint_chunk(
        self,
        delivery_id: int,
        lease_token: str,
        chunk_index: int,
        message_id: int,
        *,
        now: datetime,
    ) -> MarketAlertDelivery:
        item = await self._delivery(delivery_id)
        _assert_claim(item, lease_token, now, "sending")
        if chunk_index < item.chunks_sent:
            if chunk_index >= 0 and item.message_ids[chunk_index] == message_id:
                return item
            raise ValueError("A checkpoint conflicts with the accepted Telegram message")
        if chunk_index != item.chunks_sent or chunk_index >= len(item.chunks):
            raise ValueError("Alert chunks must be checkpointed in order")
        item.message_ids = [*item.message_ids, message_id]
        item.chunks_sent += 1
        await self.session.flush()
        return item

    async def finish_delivery(
        self,
        delivery_id: int,
        lease_token: str,
        *,
        status: str,
        now: datetime,
        error: str | None = None,
        retry_at: datetime | None = None,
    ) -> MarketAlertDelivery:
        if status not in {"delivered", "failed", "unknown", "expired"}:
            raise ValueError("Invalid terminal delivery status")
        item = await self._delivery(delivery_id)
        _assert_claim(item, lease_token, now, "sending")
        if status == "delivered" and item.chunks_sent != len(item.chunks):
            raise ValueError("Cannot complete an alert before every chunk is checkpointed")
        item.status = status
        item.last_error = error
        item.next_attempt_at = retry_at if status == "failed" else None
        item.delivered_at = now if status == "delivered" else None
        item.lease_token = None
        item.lease_expires_at = None
        await self.session.flush()
        return item

    async def recover_expired(self, *, now: datetime) -> dict[str, int]:
        decisions = list(
            (
                await self.session.execute(
                    select(MarketAlertDecision)
                    .where(
                        MarketAlertDecision.status == "assessing",
                        MarketAlertDecision.lease_expires_at <= now,
                    )
                    .order_by(MarketAlertDecision.id)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        for item in decisions:
            # A lost response may have been billed; keep the reserved amount consumed.
            await self._settle(item, None, now)
            item.status = "failed"
            item.last_error = "assessment_lease_expired"
            item.next_attempt_at = now
            item.lease_token = None
            item.lease_expires_at = None
        unknown = await self.session.execute(
            update(MarketAlertDelivery)
            .where(
                MarketAlertDelivery.status == "sending",
                MarketAlertDelivery.lease_expires_at <= now,
            )
            .values(
                status="unknown",
                last_error="delivery_lease_expired",
                lease_token=None,
                lease_expires_at=None,
                next_attempt_at=None,
            )
        )
        expired = await self.session.execute(
            update(MarketAlertDelivery)
            .where(
                MarketAlertDelivery.status.in_(("pending", "failed")),
                MarketAlertDelivery.expires_at <= now,
            )
            .values(status="expired", next_attempt_at=None)
        )
        await self.session.flush()
        return {
            "decisions": len(decisions),
            "unknown": unknown.rowcount,
            "expired": expired.rowcount,
        }

    async def list_recent_events(
        self, *, since: datetime, limit: int = 20
    ) -> list[MarketAlertEvent]:
        return list(
            (
                await self.session.scalars(
                    select(MarketAlertEvent)
                    .where(MarketAlertEvent.updated_at >= since)
                    .order_by(MarketAlertEvent.updated_at.desc(), MarketAlertEvent.id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def list_recent_decisions(
        self, *, since: datetime, limit: int = 20
    ) -> list[MarketAlertDecision]:
        return list(
            (
                await self.session.scalars(
                    select(MarketAlertDecision)
                    .where(MarketAlertDecision.created_at >= since)
                    .order_by(MarketAlertDecision.created_at.desc(), MarketAlertDecision.id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def list_recent_deliveries(
        self, *, since: datetime, limit: int = 20
    ) -> list[MarketAlertDelivery]:
        return list(
            (
                await self.session.scalars(
                    select(MarketAlertDelivery)
                    .where(MarketAlertDelivery.created_at >= since)
                    .order_by(MarketAlertDelivery.created_at.desc(), MarketAlertDelivery.id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def latest_sentiment(
        self, ticker: str, provider: str, model_revision: str
    ) -> MarketSentimentSnapshot | None:
        return (
            await self.session.execute(
                select(MarketSentimentSnapshot)
                .where(
                    MarketSentimentSnapshot.ticker == ticker,
                    MarketSentimentSnapshot.provider == provider,
                    MarketSentimentSnapshot.model_revision == model_revision,
                )
                .order_by(MarketSentimentSnapshot.as_of.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def save_sentiment(
        self,
        *,
        ticker: str,
        provider: str,
        model_revision: str,
        as_of: datetime,
        watermark: str,
        windows: dict[str, Any],
        state: dict[str, Any] | None = None,
        streak: int = 0,
        gaps: Sequence[str] = (),
    ) -> MarketSentimentSnapshot:
        await self.session.execute(
            insert(MarketSentimentSnapshot)
            .values(
                ticker=ticker,
                provider=provider,
                model_revision=model_revision,
                as_of=as_of,
                watermark=watermark,
                windows=windows,
                state=state or {},
                streak=streak,
                gaps=list(gaps),
            )
            .on_conflict_do_nothing(constraint="uq_sentiment_snapshot_window")
        )
        return (
            await self.session.execute(
                select(MarketSentimentSnapshot).where(
                    MarketSentimentSnapshot.ticker == ticker,
                    MarketSentimentSnapshot.provider == provider,
                    MarketSentimentSnapshot.model_revision == model_revision,
                    MarketSentimentSnapshot.as_of == as_of,
                )
            )
        ).scalar_one()
