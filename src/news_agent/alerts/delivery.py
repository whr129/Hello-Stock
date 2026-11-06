"""Send committed market alerts; uncertain Telegram outcomes are never replayed."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from telegram import Bot
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError

from news_agent.alerts.decision import JevDecision
from news_agent.alerts.destination import resolve_alert_destination
from news_agent.alerts.evidence import ArticleEvidence, latest_evidence
from news_agent.alerts.policy import (
    DeliveryGuard,
    DeliveryPolicyConfig,
    guard_delivery,
    is_material_update,
)
from news_agent.alerts.storage import (
    AlertRepository,
    MarketAlertDecision,
    MarketAlertDelivery,
    MarketAlertEvent,
    StaleClaimError,
)
from news_agent.settings import Settings
from news_agent.storage.models import Article

logger = logging.getLogger(__name__)


class MarketAlertDispatcher:
    def __init__(
        self,
        session_factory: Any,
        settings: Settings,
        *,
        bot_factory: Callable[..., Any] = Bot,
        repository_type: type = AlertRepository,
        delivery_guard: Callable[[Any, int], Any] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.bot_factory = bot_factory
        self.repository_type = repository_type
        self.delivery_guard = delivery_guard or self._recheck_delivery

    async def dispatch_once(self, *, limit: int = 10) -> int:
        if (
            not self.settings.market_alerts_enabled
            or self.settings.market_alert_shadow_mode
            or not self.settings.telegram_bot_token
        ):
            return 0
        destination = await resolve_alert_destination(self.session_factory, self.settings)
        if not destination:
            return 0
        async with self.session_factory() as session:
            await self.repository_type(session).recover_expired(now=datetime.now(UTC))
            await session.commit()
        delivered = 0
        for _ in range(limit):
            async with self.session_factory() as session:
                item = await self.repository_type(session).claim_delivery(
                    now=datetime.now(UTC),
                    lease_seconds=self.settings.market_alert_lease_seconds,
                    max_attempts=self.settings.market_alert_max_attempts,
                )
                await session.commit()
            if item is None:
                break
            if item.destination != destination:
                await self._finish(item, "expired", error="destination_changed")
                continue
            request_started = False
            try:
                if not await self.delivery_guard(item, destination):
                    continue
                async with self.bot_factory(token=self.settings.telegram_bot_token) as bot:
                    for index in range(item.chunks_sent, len(item.chunks)):
                        if item.expires_at <= datetime.now(UTC):
                            await self._finish(item, "expired", error="alert_expired")
                            break
                        request_started = True
                        message = await bot.send_message(
                            chat_id=item.destination,
                            text=item.chunks[index],
                            disable_web_page_preview=True,
                            connect_timeout=10,
                            read_timeout=15,
                            write_timeout=15,
                        )
                        async with self.session_factory() as session:
                            await self.repository_type(session).checkpoint_chunk(
                                item.id, item.lease_token, index, message.message_id,
                                now=datetime.now(UTC),
                            )
                            await session.commit()
                        request_started = False
                    else:
                        delivered += int(await self._finish(item, "delivered"))
            except RetryAfter as error:
                delay = error.retry_after
                seconds = delay.total_seconds() if isinstance(delay, timedelta) else delay
                await self._finish(
                    item, "failed", error="telegram_rate_limited",
                    retry_at=datetime.now(UTC) + timedelta(seconds=max(seconds, 1)),
                )
            except (BadRequest, Forbidden):
                await self._finish(item, "expired", error="telegram_rejected")
            except NetworkError:
                await self._finish(
                    item, "unknown" if request_started else "failed",
                    error=(
                        "telegram_acceptance_unknown" if request_started else "telegram_unavailable"
                    ),
                    retry_at=datetime.now(UTC) + timedelta(seconds=30),
                )
            except asyncio.CancelledError:
                await asyncio.shield(self._finish(
                    item, "unknown" if request_started else "failed", error="delivery_cancelled",
                    retry_at=datetime.now(UTC) + timedelta(seconds=5),
                ))
                raise
            except StaleClaimError:
                # The lease recovery path owns this outcome; a stale worker must stop.
                logger.warning("market alert delivery claim expired id=%s", item.id)
            except TelegramError:
                await self._finish(
                    item, "unknown" if request_started else "failed",
                    error="telegram_outcome_unknown",
                    retry_at=datetime.now(UTC) + timedelta(seconds=30),
                )
            except Exception as error:
                # The request may have succeeded before persistence failed.
                logger.error(
                    "market alert delivery interrupted id=%s error_type=%s",
                    item.id, type(error).__name__,
                )
                try:
                    await self._finish(
                        item, "unknown" if request_started else "failed",
                        error=(
                            "delivery_checkpoint_unknown"
                            if request_started else "policy_unavailable"
                        ),
                        retry_at=datetime.now(UTC) + timedelta(seconds=30),
                    )
                except Exception:
                    logger.error("market alert recovery deferred id=%s", item.id)
        return delivered

    async def _recheck_delivery(self, item: Any, destination: int) -> bool:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            decision = await session.get(MarketAlertDecision, item.decision_id)
            event = await session.get(MarketAlertEvent, item.event_id)
            if decision is None or event is None:
                guard = None
            elif event.revision != item.revision:
                guard = DeliveryGuard(False, "superseded_event")
            else:
                judgment = JevDecision.model_validate(decision.result["judgment"])
                evidence = latest_evidence([
                    ArticleEvidence.model_validate(row)
                    for row in decision.input_payload["state"]["evidence"]
                ])
                article_ids = [int(row.article_id) for row in evidence if row.article_id.isdigit()]
                versions = dict((await session.execute(
                    select(Article.id, Article.revision).where(Article.id.in_(article_ids))
                )).all())
                if len(article_ids) != len(evidence) or any(
                    versions.get(int(row.article_id)) != row.revision for row in evidence
                ):
                    await session.commit()
                    await self._finish(item, "expired", error="superseded_evidence")
                    return False
                # Serialize policy checks with other dispatchers for this destination.
                lock_key = int.from_bytes(
                    hashlib.sha256(f"recipient:{destination}".encode()).digest()[:8],
                    "big", signed=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
                recent = list(await session.scalars(
                    select(MarketAlertDelivery).where(
                        MarketAlertDelivery.destination == destination,
                        MarketAlertDelivery.id != item.id,
                        MarketAlertDelivery.status.in_(
                            ("pending", "sending", "delivered", "unknown")
                        ),
                        MarketAlertDelivery.created_at > now - timedelta(hours=2),
                    )
                ))
                same = [
                    row.delivered_at or row.created_at
                    for row in recent if row.event_id == item.event_id
                ]
                config = DeliveryPolicyConfig.from_settings(self.settings).model_copy(
                    update={"chat_id": destination}
                )
                guard = guard_delivery(
                    judgment, evidence, config=config, now=now,
                    last_delivered_at=max(same, default=None),
                    recent_deliveries=[row.delivered_at or row.created_at for row in recent],
                    material_update=is_material_update(
                        judgment, event_revision=item.revision,
                        delivery_history=[
                            {"revision": row.revision, "status": row.status}
                            for row in recent if row.event_id == item.event_id
                        ],
                    ),
                )
            await session.commit()
        if guard is not None and guard.allowed:
            return True
        retry_at = guard.not_before if guard else None
        if retry_at is not None and retry_at < item.expires_at:
            await self._finish(item, "failed", error=guard.reason, retry_at=retry_at)
        else:
            await self._finish(item, "expired", error=guard.reason if guard else "missing_decision")
        return False

    async def _finish(
        self, item: Any, status: str, *, error: str | None = None,
        retry_at: datetime | None = None,
    ) -> bool:
        try:
            async with self.session_factory() as session:
                await self.repository_type(session).finish_delivery(
                    item.id, item.lease_token, status=status, now=datetime.now(UTC),
                    error=error, retry_at=retry_at,
                )
                await session.commit()
        except StaleClaimError:
            logger.warning("market alert delivery claim no longer owned id=%s", item.id)
            return False
        return True
