from news_agent.ingestion.dedupe import content_hash, normalize_title


def test_normalize_title_collapses_spaces_and_case() -> None:
    assert normalize_title("  Big   News Today ") == "big news today"


def test_content_hash_is_stable() -> None:
    assert content_hash("A", "B") == content_hash(" a ", " b ")
