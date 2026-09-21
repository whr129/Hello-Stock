from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from news_agent.research.schemas import CandidateExplanation
from news_agent.scheduler import reports
from news_agent.settings import Settings

NOW = datetime(2026, 9, 20, 22, tzinfo=UTC)  # 18:00 Toronto


@pytest.fixture
def delivery(monkeypatch):
    runs = {}
    session = AsyncMock()
    session.__aenter__.return_value = session

    async def get(report_date):
        return runs.get(report_date)

    async def start(**kwargs):
        run = SimpleNamespace(
            id=len(runs) + 1, status="running", run_metadata=dict(kwargs["metadata"]),
        )
        runs[kwargs["trigger"]] = run
        return run

    async def update(run_id, metadata):
        next(run for run in runs.values() if run.id == run_id).run_metadata.update(metadata)

    async def finish(run_id, *, status, summary):
        next(run for run in runs.values() if run.id == run_id).status = status

    repository = Mock(
        lock_daily_research_report=AsyncMock(return_value=True),
        get_daily_research_report=AsyncMock(side_effect=get),
        start=AsyncMock(side_effect=start),
        update_metadata=AsyncMock(side_effect=update),
        finish=AsyncMock(side_effect=finish),
    )
    conversations = Mock(latest_user_chat_id=AsyncMock(return_value=123))
    bot = AsyncMock()
    bot.__aenter__.return_value = bot
    bot_factory = Mock(return_value=bot)
    monkeypatch.setattr(reports, "RuntimeRunRepository", lambda _: repository)
    monkeypatch.setattr(reports, "ConversationEventRepository", lambda _: conversations)
    monkeypatch.setattr(reports, "Bot", bot_factory)
    settings = Settings(_env_file=None, telegram_bot_token="123:fake")
    factory = Mock(return_value=session)

    def new_service():
        service = reports.DailyResearchReportService(factory, settings)
        service._build_report = AsyncMock(return_value="Daily research report")
        return service

    return SimpleNamespace(
        runs=runs, repository=repository, conversations=conversations, bot=bot,
        bot_factory=bot_factory, settings=settings, factory=factory, session=session,
        new_service=new_service,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("now", "report_time", "due"),
    [
        (datetime(2026, 1, 20, 22, 59, tzinfo=UTC), "18:00", False),
        (datetime(2026, 1, 20, 23, tzinfo=UTC), "18:00", True),
        (NOW - timedelta(minutes=1), "18:00", False),
        (NOW, "18:00", True),
        (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "02:30", False),
        (datetime(2026, 3, 8, 7, tzinfo=UTC), "02:30", True),
    ],
)
async def test_daily_report_uses_local_time_and_dst(delivery, now, report_time, due):
    delivery.settings.daily_research_report_time = report_time
    await delivery.new_service().deliver_if_due(now)
    assert delivery.bot.send_message.await_count == int(due)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["disabled", "no_token", "no_chat", "locked"])
async def test_daily_report_skips_without_delivery_prerequisites(delivery, reason):
    if reason == "disabled":
        delivery.settings.daily_research_report_enabled = False
    elif reason == "no_token":
        delivery.settings.telegram_bot_token = ""
    elif reason == "no_chat":
        delivery.conversations.latest_user_chat_id.return_value = None
    else:
        delivery.repository.lock_daily_research_report.return_value = False
    await delivery.new_service().deliver_if_due(NOW)
    delivery.bot_factory.assert_not_called()
    delivery.repository.start.assert_not_awaited()
    if reason in {"disabled", "no_token"}:
        delivery.factory.assert_not_called()
    elif reason == "locked":
        delivery.repository.get_daily_research_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_report_configured_chat_and_durable_once_per_day(delivery):
    delivery.settings.daily_research_report_chat_id = -987
    await delivery.new_service().deliver_if_due(NOW)
    await delivery.new_service().deliver_if_due(NOW + timedelta(minutes=10))
    delivery.bot.send_message.assert_awaited_once_with(
        chat_id=-987, text="Daily research report",
    )
    delivery.conversations.latest_user_chat_id.assert_not_awaited()
    run = delivery.runs["2026-09-20"]
    assert run.status == "completed"
    assert run.run_metadata["delivery_status"] == "delivered"
    await delivery.new_service().deliver_if_due(NOW + timedelta(days=1))
    assert delivery.bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_fall_dst_repeated_hour_does_not_duplicate_report(delivery):
    delivery.settings.daily_research_report_time = "01:30"
    await delivery.new_service().deliver_if_due(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    await delivery.new_service().deliver_if_due(datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    assert delivery.bot.send_message.await_count == 1
    assert list(delivery.runs) == ["2026-11-01"]


@pytest.mark.asyncio
async def test_daily_report_retries_with_backoff_and_stops_at_limit(delivery):
    delivery.settings.daily_research_report_max_attempts = 2
    delivery.bot.send_message.side_effect = RuntimeError("temporary failure")
    await delivery.new_service().deliver_if_due(NOW)
    run = delivery.runs["2026-09-20"]
    assert run.status == "failed"
    assert run.run_metadata["delivery_error"] == "temporary failure"
    await delivery.new_service().deliver_if_due(NOW + timedelta(seconds=299))
    assert delivery.bot.send_message.await_count == 1
    await delivery.new_service().deliver_if_due(NOW + timedelta(seconds=300))
    await delivery.new_service().deliver_if_due(NOW + timedelta(seconds=600))
    assert delivery.bot.send_message.await_count == 2
    assert run.run_metadata["attempts"] == 2
    assert run.run_metadata["chunks_sent"] == 0


@pytest.mark.asyncio
async def test_daily_report_resumes_partial_chunks_without_rebuilding(delivery, monkeypatch):
    monkeypatch.setattr(reports, "split_telegram_message", lambda _: ["first", "second", "third"])
    delivery.bot.send_message.side_effect = [None, RuntimeError("retry"), None, None]
    await delivery.new_service().deliver_if_due(NOW)
    assert delivery.runs["2026-09-20"].run_metadata["chunks_sent"] == 1
    retry = delivery.new_service()
    await retry.deliver_if_due(NOW + timedelta(seconds=300))
    retry._build_report.assert_not_awaited()
    assert [call.kwargs["text"] for call in delivery.bot.send_message.await_args_list] == [
        "first", "second", "second", "third",
    ]
    assert delivery.runs["2026-09-20"].status == "completed"
    assert delivery.runs["2026-09-20"].run_metadata["chunks_sent"] == 3
    assert delivery.runs["2026-09-20"].run_metadata["delivery_error"] is None


@pytest.mark.asyncio
async def test_report_generation_failure_is_recorded_and_retried(delivery):
    service = delivery.new_service()
    service._build_report.side_effect = RuntimeError("generation failed")
    await service.deliver_if_due(NOW)
    delivery.bot_factory.assert_not_called()
    assert delivery.runs["2026-09-20"].status == "failed"
    assert delivery.runs["2026-09-20"].run_metadata["delivery_error"] == "generation failed"
    await delivery.new_service().deliver_if_due(NOW + timedelta(seconds=300))
    assert delivery.runs["2026-09-20"].status == "completed"
    delivery.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [True, False])
async def test_daily_report_uses_fresh_signals_and_validated_evidence(delivery, monkeypatch, empty):
    snapshot = object()
    repository = Mock(
        fetch_top_candidates=AsyncMock(return_value=[] if empty else [snapshot]),
        update_snapshot_evidence=AsyncMock(),
    )
    monkeypatch.setattr(reports, "MarketSignalRepository", lambda _: repository)
    explanations = [] if empty else [
        CandidateExplanation(
            ticker=ticker, theme=None, rank=index, total_score=80, components={}, evidence=[],
            weak_evidence=[], snapshot_id=index, evidence_strength=strength,
        )
        for index, (ticker, strength) in enumerate(
            [("STRONG", "strong"), ("WEAK", "weak"), ("DEVELOPING", "developing")], start=1,
        )
    ]
    unvalidated = [replace(item, evidence_strength="strong") for item in explanations]
    explain = Mock(return_value=unvalidated)
    validate = AsyncMock(return_value=explanations)
    monkeypatch.setattr(reports, "explain_candidates", explain)
    monkeypatch.setattr(reports, "validate_candidate_links", validate)
    delivery.settings.signal_allow_developing_default = False
    service = reports.DailyResearchReportService(delivery.factory, delivery.settings)
    text = await service._build_report("2026-09-20", NOW)
    repository.fetch_top_candidates.assert_awaited_once_with(
        window="24h", limit=9, since=NOW - timedelta(hours=24),
    )
    explain.assert_called_once_with(
        [] if empty else [snapshot],
        min_strong_sources=delivery.settings.signal_min_strong_evidence_sources,
    )
    validate.assert_awaited_once_with(
        unvalidated, recheck_hours=delivery.settings.evidence_link_recheck_hours,
    )
    assert "Daily research report — 2026-09-20" in text
    assert "Not financial advice" in text
    if empty:
        assert "No market attention candidates" in text
        repository.update_snapshot_evidence.assert_not_awaited()
    else:
        assert "STRONG" in text
        assert "WEAK" not in text
        assert "DEVELOPING" not in text
        assert repository.update_snapshot_evidence.await_count == 3


@pytest.mark.asyncio
async def test_daily_report_preserves_financial_guardrails(delivery, monkeypatch):
    repository = Mock(fetch_top_candidates=AsyncMock(return_value=[]))
    monkeypatch.setattr(reports, "MarketSignalRepository", lambda _: repository)
    monkeypatch.setattr(reports, "format_candidates", lambda *args, **kwargs: "You should buy TSLA")
    service = reports.DailyResearchReportService(delivery.factory, delivery.settings)

    text = await service._build_report("2026-09-20", NOW)

    assert "You should buy" not in text
    assert "cannot provide buy/sell recommendations" in text
