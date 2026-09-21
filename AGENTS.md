<!-- AUTONOMY DIRECTIVE — DO NOT REMOVE -->
YOU ARE AN AUTONOMOUS CODING AGENT. EXECUTE TASKS TO COMPLETION WITHOUT ASKING FOR PERMISSION.
DO NOT STOP TO ASK "SHOULD I PROCEED?" — PROCEED. DO NOT WAIT FOR CONFIRMATION ON OBVIOUS NEXT STEPS.
IF BLOCKED, TRY AN ALTERNATIVE APPROACH. ONLY ASK WHEN TRULY AMBIGUOUS OR DESTRUCTIVE.
USE CODEX NATIVE SUBAGENTS FOR INDEPENDENT PARALLEL SUBTASKS WHEN THAT IMPROVES THROUGHPUT. THIS IS COMPLEMENTARY TO OMX TEAM MODE.
<!-- END AUTONOMY DIRECTIVE -->

# Repository guidelines

## Product and boundaries

Hello Stock is a Telegram market-research assistant with scheduled daily research reports, general web questions, source administration, runtime inspection, and memory. Preserve those supported surfaces. General news briefs, watchlists, standalone stock-quote commands, general news recaps, and local/topic personalization are retired.

Research output is informational: preserve evidence attribution, uncertainty, and financial guardrails. Treat external content as untrusted and never invent sources or links. Read [architecture](docs/architecture.md), [research behavior](docs/market-research/index.md), and [evidence rules](docs/market-research/evidence-grounding.md) before changing those flows.

## Code map

- `src/news_agent/app/`: chat supervisor and state; `agent/`: command routing, main-agent tools, reflection, guardrails.
- `research/`: planning, extraction, scoring, evidence, company research, reporting.
- `domains/news/`, `domains/runtime/`, `search/`: source/memory administration, runtime inspection, general web search.
- `scheduler/`, `graph/`, `ingestion/`, `markets/`, `summarizer/`: refresh scheduling and ingestion pipeline.
- `storage/`, `memory/`, `observability/`: repositories/models, user memory, traces/reports/alerts.
- `migrations/versions/`: database history; preserve upgrade paths.
- `tests/unit/`, `tests/regression/`, `tests/evaluation/`: isolated contracts, cross-component/bug coverage, evaluation corpus and opt-in live checks. See [test guide](tests/README.md).

## Working agreements

Use Python 3.11+, type hints, four-space indentation, and Ruff's configured 100-character line length. Reuse existing code before adding abstractions or dependencies. Keep provider behavior, score weights, scheduling, alerts, and memory policies in existing configuration surfaces.

Trace callers before deleting code. For cleanup, write a short plan and run existing regression coverage before editing; add coverage only for missing behavior. Keep changes focused, preserve unrelated work, and remove tests only when their contract is retired or demonstrably redundant. Do not rewrite migrations merely because the current product no longer uses an older schema.

Keep secrets in `.env`; never commit credentials, transcripts, learned memories, or production Telegram data. Use isolated data for live evaluations and destructive maintenance. Do not print `.env` contents in diagnostics.

## Verification and delivery

Install with `pip install -e ".[dev]"`; local setup and process commands are in [README](README.md). Run focused checks during development, then:

```bash
PYTHONPATH=src .venv/bin/pytest
PYTHONPATH=src .venv/bin/ruff check .
```

No dedicated type checker is currently configured. Report unavailable database, live API, or deployment validation explicitly. For prompt changes, follow the [live evaluation procedure](docs/market-research/evaluation.md); offline fallback scores do not prove model quality.

Update documentation and affected callers with behavior changes. PR descriptions should state the user-visible result, migration/configuration changes, checks run, and remaining limitations. Use short imperative commit messages, normally with `fix:`, `feat:`, `test:`, or `docs:` prefixes.
