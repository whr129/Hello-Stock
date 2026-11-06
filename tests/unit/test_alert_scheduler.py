import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from news_agent.scheduler import jobs
from news_agent.settings import Settings


@pytest.mark.asyncio
async def test_slow_bulk_refresh_does_not_block_collection_decisions_or_delivery(monkeypatch):
    from news_agent.alerts import collector, delivery, service

    started = {name: asyncio.Event() for name in ('bulk', 'collect', 'assess', 'deliver')}
    never = asyncio.Event()

    async def bulk(settings):
        started['bulk'].set()
        await never.wait()

    async def mark(name):
        started[name].set()
        return {}

    close = AsyncMock()
    engine = SimpleNamespace(dispose=AsyncMock())
    factory = SimpleNamespace(kw={'bind': engine})
    monkeypatch.setattr(jobs, '_bulk_scheduler_loop', bulk)
    monkeypatch.setattr(jobs, 'create_session_factory', lambda _: factory)
    monkeypatch.setattr(jobs, 'RuntimeTraceService', lambda *_: SimpleNamespace(
        ensure_run=AsyncMock(return_value=1), finish_run=AsyncMock(), record_error=AsyncMock(),
    ))
    monkeypatch.setattr(collector, 'FastNewsCollector', lambda *_: SimpleNamespace(
        collect_once=lambda: mark('collect'),
    ))
    monkeypatch.setattr(service, 'MarketAlertService', lambda *_: SimpleNamespace(
        process_once=lambda: mark('assess'), aclose=close, settings=settings,
    ))
    monkeypatch.setattr(delivery, 'MarketAlertDispatcher', lambda *_: SimpleNamespace(
        dispatch_once=lambda: mark('deliver'),
    ))
    settings = Settings(_env_file=None, market_alerts_enabled=True, openai_api_key='')
    task = asyncio.create_task(jobs.scheduler_loop(settings))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 5)
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    close.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_periodic_task_records_failure_and_keeps_running():
    succeeded = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError('do not log remote data')
        succeeded.set()
        return {'saved': 1}

    trace = SimpleNamespace(
        ensure_run=AsyncMock(return_value=1), finish_run=AsyncMock(), record_error=AsyncMock(),
    )
    task = asyncio.create_task(jobs._market_periodic('test-alert-loop', operation, 0.01, trace))
    try:
        await asyncio.wait_for(succeeded.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls >= 2
    assert trace.record_error.await_args.kwargs['error_message'] == 'ValueError'
