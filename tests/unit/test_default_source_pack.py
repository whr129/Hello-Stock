import json
from pathlib import Path

SOURCE_PACK_PATH = Path("docs/market-research/default-sources.json")


def test_default_source_pack_parses_and_has_required_fields() -> None:
    sources = json.loads(SOURCE_PACK_PATH.read_text())

    assert isinstance(sources, list)
    assert sources

    seen_names: set[str] = set()
    categories: set[str] = set()
    for source in sources:
        assert isinstance(source, dict)
        assert isinstance(source.get("name"), str) and source["name"].strip()
        assert source["name"] not in seen_names
        seen_names.add(source["name"])

        provider = source.get("provider")
        assert provider in {"rss", "alpha_vantage", "finnhub", "polygon"}
        if provider == "rss":
            assert isinstance(source.get("feed_url"), str) and source["feed_url"].startswith(
                "https://"
            )
        else:
            assert isinstance(source.get("external_account"), str)
        assert isinstance(source.get("category"), str) and source["category"].strip()
        categories.add(source["category"])

        trust_score = source.get("trust_score")
        assert isinstance(trust_score, int | float)
        assert 0 <= trust_score <= 1

        config = source.get("config")
        assert isinstance(config, dict)
        assert isinstance(config.get("max_items"), int)
        assert isinstance(config.get("max_item_age_hours"), int)
        assert isinstance(config.get("fetch_interval_seconds"), int)
        assert config.get("pipeline_tier") in {"breaking_resources", "daily_resources"}

    assert {
        "macro",
        "filings",
        "policy",
        "regulatory",
        "energy",
        "commodities",
        "company_ir",
        "market_news",
    }.issubset(categories)

    company_sources = [
        source
        for source in sources
        if source.get("category") == "company_ir"
        and (source.get("config") or {}).get("pipeline_tier") == "breaking_resources"
    ]
    assert len(company_sources) >= 50
    assert any(
        (source.get("config") or {}).get("pipeline_tier") == "daily_resources"
        for source in sources
    )
