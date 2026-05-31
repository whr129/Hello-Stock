import argparse
import json
from pathlib import Path

from news_agent.ingestion.providers import IngestProviderRegistry
from news_agent.settings import Settings
from news_agent.storage.models import Source

DEFAULT_SOURCE_PACK_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "market-research" / "default-sources.json"
)


def validate_source_pack(
    path: Path = DEFAULT_SOURCE_PACK_PATH,
    *,
    timeout_seconds: int = 15,
    write: bool = False,
    settings: Settings | None = None,
) -> dict[str, int]:
    config = settings or Settings()
    registry = IngestProviderRegistry(config)
    sources = json.loads(path.read_text())
    kept: list[dict] = []
    dropped = 0
    skipped = 0

    for index, source_config in enumerate(sources, start=1):
        source = _source_from_config(index, source_config)
        try:
            provider = registry.get(source.provider)
            items = provider.fetch_items(source, timeout_seconds=timeout_seconds)
        except ValueError as exc:
            if "api_key" in str(exc) or "API_KEY" in str(exc):
                skipped += 1
                kept.append(source_config)
                continue
            dropped += 1
            continue
        except Exception:
            dropped += 1
            continue

        if items:
            kept.append(source_config)
        else:
            dropped += 1

    if write:
        path.write_text(json.dumps(kept, indent=2) + "\n")

    return {"checked": len(sources), "kept": len(kept), "dropped": dropped, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate default market research source pack.")
    parser.add_argument("--source-pack", type=Path, default=DEFAULT_SOURCE_PACK_PATH)
    parser.add_argument("--timeout-seconds", type=int, default=15)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    result = validate_source_pack(
        args.source_pack,
        timeout_seconds=args.timeout_seconds,
        write=args.write,
    )
    print(json.dumps(result, sort_keys=True))


def _source_from_config(index: int, item: dict) -> Source:
    provider = str(item.get("provider") or "rss").strip().lower()
    external_account = str(item.get("external_account") or item.get("feed_url") or "").strip()
    config = dict(item.get("config") or {})
    if provider in {"rss", "twitter", "newsletter"} and "feed_url" not in config:
        config["feed_url"] = str(item.get("feed_url") or external_account)
    return Source(
        id=index,
        name=str(item.get("name") or external_account),
        url=external_account,
        provider=provider,
        external_account=external_account,
        config=config,
        field_mapping=dict(item.get("field_mapping") or {}),
        fetch_mode=str(item.get("fetch_mode") or "rss"),
        category=str(item.get("category") or "markets"),
        trust_score=float(item.get("trust_score") or 0.5),
    )


if __name__ == "__main__":
    main()
