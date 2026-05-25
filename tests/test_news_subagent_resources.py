import json

from news_agent.domains.news import subagent
from news_agent.domains.news.subagent import ResourceSection, _format_resource_inventory


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
