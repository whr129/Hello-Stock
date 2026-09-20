# Tests and evaluation

- `unit/`: isolated helpers, contracts, provider adapters, and evaluator judging logic.
- `regression/`: routing, workflow wiring, safety boundaries, reporting, and previously
  corrected failure cases. External services use fakes; these are not live integration tests.
- `evaluation/`: the answer-quality corpus, its loading check, and opt-in source validation.
  Repeated corpus prompts retain distinct acceptance criteria.

Run from the repository root after installing `.[dev]`:

```bash
PYTHONPATH=src .venv/bin/pytest -q
PYTHONPATH=src .venv/bin/pytest -q tests/unit
PYTHONPATH=src .venv/bin/pytest -q tests/regression
PYTHONPATH=src .venv/bin/pytest -q tests/evaluation
PYTHONPATH=src .venv/bin/ruff check .
```

The default suite runs offline; source validation is skipped unless enabled explicitly:

```bash
LIVE_SOURCE_VALIDATION=1 PYTHONPATH=src .venv/bin/pytest -q tests/evaluation/test_source_pack_live.py
```

Run answer evaluation against a migrated, populated evaluation database:

```bash
PYTHONPATH=src .venv/bin/python -m news_agent.evaluation.runner --cases tests/evaluation/market_research_cases.jsonl
```

The evaluator executes the application graph and can write conversation and research data;
use an isolated database. Live judging needs `OPENAI_API_KEY` and `EVAL_LLM_ENABLED=true`.
Deterministic fallback checks answer shape, not answer quality. See the
[evaluation guide](../docs/market-research/evaluation.md) for acceptance criteria.

The offline suite does not prove live feed availability, model answer quality, PostgreSQL
transaction behavior, or pgvector retrieval. CI separately validates database migrations.
