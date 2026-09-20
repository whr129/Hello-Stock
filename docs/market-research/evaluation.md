# Market research evaluation

Code correctness and answer quality are separate checks. [The test guide](../../tests/README.md) describes unit, regression, and evaluation folders. The versioned [prompt corpus](../../tests/evaluation/market_research_cases.jsonl) supplies prompt IDs, requests, and expected answer properties.

## Offline checks

```bash
PYTHONPATH=src .venv/bin/pytest tests/unit tests/regression tests/evaluation
```

These tests cover execution contracts, known failure cases, and corpus validity. External source-pack checks are opt-in as described in the test guide. Deterministic judge tests check answer shape and safety heuristics; they do not prove current model quality, source truth, or link reachability.

## End-to-end answer evaluation

Use a separate, migrated PostgreSQL database populated with representative research evidence. The runner invokes the real chat supervisor, creates synthetic users/chats, persists conversation/session/runtime data, and can enqueue memory work. Commands in the corpus may also update research data. Do not run it against production user data or an active production scheduler.

From the repository root with the virtualenv activated and the evaluation database configured:

```bash
news-agent-eval --cases tests/evaluation/market_research_cases.jsonl
```

`DATABASE_URL` selects the target database. `EVAL_MAX_CASES` limits the number of prompts (default 50), and `EVAL_OUTPUT_PATH` selects the output directory (default `reports/eval`). The command writes timestamped JSONL results and a Markdown summary.

Set `EVAL_LLM_ENABLED=true` and configure `OPENAI_API_KEY` to request the LLM judge; `EVAL_MODEL` overrides its model. Evaluation can incur API costs and use network access. Disabling the judge does **not** disable the answering agent's own LLM/web calls.

Each judgment records `live_llm` or `deterministic_fallback`. Reports include the effective mode, judge model, prompt revision, live/fallback counts, average relevance/grounding/usefulness, and a suggested improvement target. API failures or invalid judge output can fall back even when the judge is enabled. A fallback or mixed run is not a passing live quality gate.

## Rubric and change procedure

The judge scores relevance, specificity, ticker/theme correctness, evidence quality, freshness, source attribution/link validity, grounding, explainability, usefulness, safety, and concision. Expected case properties determine what is relevant. Failure tags identify missing evidence, wrong tickers, stale data, invented sources, broken links, weak ranking explanations, excessive candidates, and missing safety language.

For model-facing prompt changes:

1. Run the same versioned corpus before and after the change using equivalent stored evidence and model configuration.
2. Check that every judgment used the live LLM, then inspect failed cases and critical grounding/safety failures directly.
3. Require at least 90% passing cases, no hallucinated-source, wrong-ticker, broken-link, or investment-advice failures, and no decline in mean relevance, grounding, or usefulness.
4. Improve the layer responsible for the most common failure; rerun the same cases and add a focused deterministic regression when a code defect is found.

These thresholds are review criteria, not an automated deployment gate. An LLM judge is imperfect; manually verify critical source and safety claims. Generated reports are run artifacts, not the canonical test corpus.
