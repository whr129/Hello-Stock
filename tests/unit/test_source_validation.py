import json
import sys
from pathlib import Path
from urllib.error import HTTPError

from news_agent.maintenance.validate_sources import main, validate_source_pack
from news_agent.settings import Settings


class FakeProvider:
    def __init__(self, outcomes: dict[str, list[object]]) -> None:
        self.outcomes = {name: list(items) for name, items in outcomes.items()}
        self.calls: list[str] = []

    def fetch_items(self, source, timeout_seconds):
        self.calls.append(source.name)
        outcome = self.outcomes[source.name].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def prepare_pack(tmp_path: Path, monkeypatch, outcomes: dict[str, list[object]]):
    path = tmp_path / "sources.json"
    sources = [
        {"name": name, "provider": "rss", "feed_url": f"https://example.test/{name}"}
        for name in outcomes
    ]
    path.write_text(json.dumps(sources))
    provider = FakeProvider(outcomes)

    class Registry:
        def __init__(self, settings):
            pass

        def get(self, name):
            return provider

    monkeypatch.setattr("news_agent.maintenance.validate_sources.IngestProviderRegistry", Registry)
    return path, provider


def http_error(status: int) -> HTTPError:
    return HTTPError("https://example.test/feed", status, "source error", None, None)


def test_legacy_validator_keeps_existing_write_behavior(tmp_path, monkeypatch) -> None:
    path, _ = prepare_pack(
        tmp_path,
        monkeypatch,
        {
            "healthy": [[object()]],
            "missing_key": [ValueError("provider requires API_KEY")],
            "unauthorized": [http_error(401)],
            "forbidden": [http_error(403)],
            "temporary": [TimeoutError()],
            "empty": [[]],
        },
    )

    result = validate_source_pack(path, write=True, settings=Settings(_env_file=None))

    assert result == {"checked": 6, "kept": 2, "dropped": 4, "skipped": 1}
    assert [source["name"] for source in json.loads(path.read_text())] == [
        "healthy",
        "missing_key",
    ]


def test_targeted_cleanup_removes_only_confirmed_401(tmp_path, monkeypatch) -> None:
    path, provider = prepare_pack(
        tmp_path,
        monkeypatch,
        {
            "unauthorized": [http_error(401), http_error(401)],
            "recovers_empty": [http_error(401), []],
            "changes_to_forbidden": [http_error(401), http_error(403)],
            "changes_to_timeout": [http_error(401), TimeoutError()],
            "forbidden": [http_error(403)],
            "not_found": [http_error(404)],
            "rate_limited": [http_error(429)],
            "temporary": [TimeoutError()],
            "empty": [[]],
            "missing_key": [ValueError("provider requires API_KEY")],
            "bad_config": [ValueError("bad source configuration")],
            "error_text": [RuntimeError("upstream message contained 401")],
            "healthy": [[object()]],
        },
    )

    result = validate_source_pack(
        path, write=True, remove_unauthorized=True, settings=Settings(_env_file=None)
    )

    assert result == {"checked": 13, "kept": 12, "dropped": 1, "skipped": 1}
    assert [source["name"] for source in json.loads(path.read_text())] == [
        name for name in provider.outcomes if name != "unauthorized"
    ]
    assert provider.calls.count("unauthorized") == 2
    assert provider.calls.count("recovers_empty") == 2
    assert provider.calls.count("forbidden") == 1


def test_targeted_cleanup_is_read_only_without_write(tmp_path, monkeypatch) -> None:
    path, _ = prepare_pack(
        tmp_path, monkeypatch, {"unauthorized": [http_error(401), http_error(401)]}
    )
    original = path.read_bytes()

    result = validate_source_pack(
        path, remove_unauthorized=True, settings=Settings(_env_file=None)
    )

    assert result["dropped"] == 1
    assert path.read_bytes() == original


def test_cli_passes_targeted_cleanup_options(tmp_path, monkeypatch, capsys) -> None:
    path, _ = prepare_pack(
        tmp_path, monkeypatch, {"unauthorized": [http_error(401), http_error(401)]}
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate-sources", "--source-pack", str(path), "--remove-unauthorized", "--write"],
    )

    main()

    assert json.loads(path.read_text()) == []
    assert json.loads(capsys.readouterr().out)["dropped"] == 1
