from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from news_agent.observability.runtime import RefreshReportService
from news_agent.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "errors"),
    [("completed", []), ("failed", ["Source request timed out"])],
)
async def test_refresh_report_is_stored_without_telegram_delivery(monkeypatch, status, errors):
    monkeypatch.setenv("REFRESH_REPORT_ENABLED", "true")
    Settings(_env_file=None, telegram_bot_token="123:test")
    bot = Mock(side_effect=AssertionError("Refresh reports must not construct a Telegram bot"))
    monkeypatch.setattr("news_agent.observability.runtime.Bot", bot)
    session = AsyncMock()
    session.__aenter__.return_value = session
    repository = Mock(
        get=AsyncMock(
            return_value=SimpleNamespace(started_at=datetime.now(UTC) - timedelta(seconds=5)),
        ),
        update_metadata=AsyncMock(),
    )
    monkeypatch.setattr(
        "news_agent.observability.runtime.RuntimeRunRepository", lambda _: repository,
    )
    service = RefreshReportService(lambda: session)
    report = await service.record(
        run_id=17,
        status=status,
        state={
            "errors": errors,
            "metadata": {
                "saved_article_count": 2,
                "accepted_article_count": 3,
                "rejected_article_count": 1,
                "duplicate_article_count": 1,
                "market_snapshot_count": 4,
                "fetch_metrics": {
                    "sources": {"attempted": 2, "succeeded": 1, "failed": 1, "items_fetched": 4},
                    "retry_count": 2,
                },
            },
        },
    )

    assert report["status"] == status
    assert report["failures"] == errors
    assert report["duration_seconds"] >= 5
    assert report["articles"] == {
        "fetched": 4, "accepted": 3, "saved": 2, "rejected": 1, "duplicates": 1,
    }
    assert report["market_snapshots"] == 4
    assert report["retry_count"] == 2
    assert report["delivery_status"] == "disabled"
    assert report["target_chat_id"] is None
    assert report["delivered_at"] is None
    assert "Refresh report: run 17" in report["text"]
    assert f"Status: {status}" in report["text"]
    assert f"Failures: {errors[0] if errors else 'none'}" in report["text"]
    repository.update_metadata.assert_awaited_once_with(
        17, {"refresh_report": report, "report_delivery_status": "disabled"},
    )
    session.commit.assert_awaited_once()
    bot.assert_not_called()
