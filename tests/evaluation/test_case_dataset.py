import json
from pathlib import Path

from news_agent.evaluation.runner import _load_cases


def test_evaluation_corpus_loads_unique_cases_with_required_fields() -> None:
    path = Path(__file__).with_name("market_research_cases.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    assert rows
    for row in rows:
        for field in ("id", "prompt", "expected"):
            assert isinstance(row[field], str) and row[field].strip()
    assert len({row["id"] for row in rows}) == len(rows)

    cases = _load_cases(path, limit=len(rows))

    assert [(case.id, case.prompt, case.expected) for case in cases] == [
        (row["id"], row["prompt"], row["expected"]) for row in rows
    ]
