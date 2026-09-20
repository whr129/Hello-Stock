import json

from news_agent.domains.news import subagent
from news_agent.domains.news.subagent import (
    NewsSubagent,
    ResourceSection,
    _format_resource_inventory,
)
from news_agent.scheduler.service import RefreshSummary


def test_format_resource_inventory_includes_counts_and_details() -> None:
    response = _format_resource_inventory(
        [
            ResourceSection(
                name="sources",
                count=2,
                details=("providers: rss: 2", "categories: macro: 1, filings: 1"),
            ),
            ResourceSection(name="articles", count=7, details=("recent: #1 Fed decision",)),
        ]
    )

    assert "Resource inventory:" in response
    assert "- sources: 2" in response
    assert "  providers: rss: 2" in response
    assert "- articles: 7" in response


def test_format_source_pack_lists_checkable_feeds(monkeypatch, tmp_path) -> None:
    source_pack_path = tmp_path / "default-sources.json"
    source_pack_path.write_text(
        json.dumps(
            [
                {
                    "name": "Fed",
                    "provider": "rss",
                    "feed_url": "https://example.com/fed.xml",
                    "category": "macro",
                    "trust_score": 0.98,
                },
                {
                    "name": "FTC",
                    "provider": "rss",
                    "feed_url": "https://example.com/ftc.xml",
                    "category": "policy",
                    "trust_score": 0.95,
                },
            ]
        )
    )
    monkeypatch.setattr(subagent, "SOURCE_PACK_PATH", source_pack_path)

    response = subagent._format_source_pack(["macro"])

    assert "Checkable source pack feeds: 1" in response
    assert "Fed [macro]" in response
    assert "https://example.com/fed.xml" in response
    assert "FTC" not in response


async def test_refresh_without_args_uses_manual_refresh() -> None:
    agent = NewsSubagent.__new__(NewsSubagent)
    agent.scheduler_control = _FakeSchedulerControl()

    result = await agent._scheduler_admin({"args": []})

    assert agent.scheduler_control.calls == ["manual_refresh"]
    assert "Refresh completed" in result["response"]
    assert result["metadata"]["pipeline"] == "manual_refresh"


async def test_refresh_specific_pipelines() -> None:
    agent = NewsSubagent.__new__(NewsSubagent)
    agent.scheduler_control = _FakeSchedulerControl()

    await agent._scheduler_admin({"args": ["market_prices"]})
    await agent._scheduler_admin({"args": ["breaking_resources"]})
    await agent._scheduler_admin({"args": ["daily_resources"]})

    assert agent.scheduler_control.calls == [
        "market_prices",
        "breaking_resources",
        "daily_resources",
    ]


async def test_refresh_pipeline_aliases() -> None:
    agent = NewsSubagent.__new__(NewsSubagent)
    agent.scheduler_control = _FakeSchedulerControl()

    await agent._scheduler_admin({"args": ["prices"]})
    await agent._scheduler_admin({"args": ["breaking"]})
    await agent._scheduler_admin({"args": ["daily"]})
    await agent._scheduler_admin({"args": ["all"]})

    assert agent.scheduler_control.calls == [
        "market_prices",
        "breaking_resources",
        "daily_resources",
        "manual_refresh",
    ]


async def test_refresh_invalid_pipeline_returns_usage_without_running() -> None:
    agent = NewsSubagent.__new__(NewsSubagent)
    agent.scheduler_control = _FakeSchedulerControl()

    result = await agent._scheduler_admin({"args": ["unknown"]})

    assert agent.scheduler_control.calls == []
    assert "Usage: /refresh" in result["response"]
    assert result["metadata"]["status"] == "invalid_pipeline"


class _FakeSchedulerControl:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def can_start_refresh(self) -> bool:
        return True

    async def run_refresh(self, job_type: str = "manual_refresh") -> RefreshSummary:
        self.calls.append(job_type)
        return RefreshSummary(
            job_type=job_type,
            saved_article_count=1,
            summary_count=0,
            market_snapshot_count=0,
            error_count=0,
            provider_counts={},
            errors=[],
        )

    def format_refresh_summary(self, summary: RefreshSummary) -> str:
        return f"Refresh completed for {summary.job_type}."
