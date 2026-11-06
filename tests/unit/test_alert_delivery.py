import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden, RetryAfter, TimedOut

from news_agent.alerts.delivery import MarketAlertDispatcher
from news_agent.settings import Settings


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def commit(self):
        pass


class Repository:
    def __init__(self, item):
        self.item = item
        self.checkpoints = []
        self.retry_at = None

    async def recover_expired(self, **_):
        return {}

    async def claim_delivery(self, **_):
        if self.item.status not in {'pending', 'failed'}:
            return None
        self.item.status = 'sending'
        return self.item

    async def checkpoint_chunk(self, item_id, token, index, message_id, **_):
        self.checkpoints.append((index, message_id))
        self.item.chunks_sent = index + 1

    async def finish_delivery(self, item_id, token, *, status, retry_at=None, **_):
        self.item.status = status
        self.retry_at = retry_at


class Bot:
    def __init__(self, failure=None):
        self.send_message = AsyncMock(side_effect=failure) if failure else AsyncMock(
            return_value=SimpleNamespace(message_id=71)
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


def setup_dispatcher(failure=None, **overrides):
    item = SimpleNamespace(
        id=1, status='pending', destination=123, lease_token='owned',
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        chunks=['already sent', 'remaining chunk'], chunks_sent=1,
    )
    repository = Repository(item)
    bot = Bot(failure)
    settings = Settings(
        _env_file=None, market_alerts_enabled=True, market_alert_shadow_mode=False,
        market_alert_chat_id=123, telegram_bot_token='isolated-test-token', **overrides,
    )
    dispatcher = MarketAlertDispatcher(
        Session, settings, bot_factory=lambda **_: bot, repository_type=lambda _: repository,
        delivery_guard=AsyncMock(return_value=True),
    )
    return dispatcher, repository, bot


@pytest.mark.asyncio
async def test_delivery_resumes_remaining_chunks_and_checkpoints_before_completion():
    dispatcher, repository, bot = setup_dispatcher()
    assert await dispatcher.dispatch_once() == 1
    assert repository.item.status == 'delivered'
    assert repository.checkpoints == [(1, 71)]
    assert bot.send_message.await_args.kwargs['text'] == 'remaining chunk'


@pytest.mark.asyncio
async def test_timeout_records_unknown_and_does_not_replay():
    dispatcher, repository, bot = setup_dispatcher(TimedOut())
    assert await dispatcher.dispatch_once() == 0
    assert await dispatcher.dispatch_once() == 0
    assert repository.item.status == 'unknown'
    assert repository.checkpoints == []
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_known_telegram_rejection_is_terminal():
    dispatcher, repository, bot = setup_dispatcher(Forbidden('rejected'))
    assert await dispatcher.dispatch_once() == 0
    assert repository.item.status == 'expired'
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_rate_limit_records_retry_without_advancing_checkpoint(monkeypatch):
    monkeypatch.setenv("PTB_TIMEDELTA", "true")
    dispatcher, repository, _ = setup_dispatcher(RetryAfter(120))
    assert await dispatcher.dispatch_once(limit=1) == 0
    assert repository.item.status == 'failed'
    assert repository.retry_at > datetime.now(UTC) + timedelta(seconds=110)
    assert not repository.checkpoints


@pytest.mark.asyncio
async def test_cancellation_preserves_unknown_outcome():
    dispatcher, repository, _ = setup_dispatcher(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await dispatcher.dispatch_once()
    assert repository.item.status == 'unknown'


@pytest.mark.asyncio
async def test_disabled_delivery_never_claims_or_sends():
    dispatcher, repository, bot = setup_dispatcher()
    dispatcher.settings.market_alert_shadow_mode = True
    assert await dispatcher.dispatch_once() == 0
    assert repository.item.status == 'pending'
    bot.send_message.assert_not_awaited()
