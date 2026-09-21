# Architecture and operations

## Processes and storage

`news-agent` starts Telegram polling in `src/news_agent/main.py`; `news-agent-scheduler` starts the independent tick loop in `scheduler/jobs.py`. Both use async SQLAlchemy sessions against PostgreSQL with pgvector. Alembic owns schema evolution.

Storage includes sources, articles, summaries and embeddings; market snapshots, entities, mentions, signal snapshots and theme memory; users, conversation events, short-term sessions, durable memories and consolidation jobs; refresh jobs, runtime runs, steps, errors and alert deliveries. The database is shared application state, not merely a disposable search cache.

## Chat flow

1. `bot/handlers.py` passes Telegram user/chat IDs and message text to `app/supervisor.py`.
2. The supervisor loads the chat's rolling message state and semantically relevant long-term memories.
3. `agent/router.py` parses explicit commands. Known commands dispatch directly to research, source administration, or runtime inspection. Other messages go to `agent/main_agent.py`.
4. The main agent uses a bounded tool loop defined in `agent/tools.py`: web search, research, runtime inspection, source/refresh/memory administration, and memory search. `search/service.py` performs general web search. Natural-language source administration is limited; use explicit commands to add, configure, or remove sources.
5. Research/main-agent answers pass financial guardrails. Answer reflection can pass, retry with a correction, or fail; retries are bounded by `ANSWER_REFLECTION_MAX_RETRIES` (default one). Exhaustion returns the available answer with a note and records the failure.
6. The supervisor persists the transcript and rolling session, queues due memory work, and completes the runtime trace. The Telegram handler splits long replies into safe message chunks.

`research/` combines deterministic planning, mention extraction, weighted scoring, evidence-strength filtering, link checking, and report formatting. Optional company web research and LLM synthesis are controlled by `RESEARCH_WEB_ENABLED` (default false); they supplement stored research. General main-agent web search is a separate capability.

## Refresh and memory flow

The scheduler checks due pipelines every `SCHEDULER_TICK_SECONDS` (default 60 seconds):

| Pipeline | Default interval | Scope |
| --- | --- | --- |
| `market_prices` | 600 seconds, during regular US market hours | Configured market universe snapshots |
| `breaking_resources` | 1,800 seconds | Breaking-tier sources |
| `daily_resources` | 86,400 seconds | Daily-tier sources |

`graph/scheduler_graph.py` and `graph/nodes.py` load due sources, fetch source/market data, apply the market-impact gate and deduplication, store embeddings, precompute summaries, extract mentions, count sector context, backfill evidence, score signals, count confident-signal context, and prune research data. Candidate filtering happens later in `research/analysis.py` when preparing answers. The final stage records job completion and errors. Individual source and ticker fetches retry with configured attempt limits and linear backoff; errors remain visible while other sources continue.

Each tick also drains queued long-term-memory jobs, delivers any due daily research report,
and runs retention cleanup. By default, every 20 new user messages makes a consolidation
batch eligible: the memory service extracts durable candidates, compares vector memories,
and adds, updates, or skips them. The short-term window defaults to 20 messages and expires
after 30 days; these values are configurable.

## Runtime visibility

`observability/runtime.py` records runs, ordered steps, errors, and refresh reports. `/runtime`, `/job`, `/trace`, and `/step` inspect this history; `/refreshreport` shows structured refresh counts, retries, failures, and delivery status.

Refresh reports are stored in runtime-run metadata without automatic Telegram delivery.
`scheduler/reports.py` sends a daily research report using recent stored 24-hour signals,
the existing evidence gates and link checks, and the candidate formatter. Configure
`DAILY_RESEARCH_REPORT_ENABLED`, `DAILY_RESEARCH_REPORT_TIME` (default `18:00`),
`DAILY_RESEARCH_REPORT_TIMEZONE` (default `America/Toronto`),
`DAILY_RESEARCH_REPORT_CHAT_ID` (`0` selects the latest user chat), and
`DAILY_RESEARCH_REPORT_MAX_CANDIDATES` (default `3`). A missing bot token or destination skips
delivery. No chat transcripts or personal memories are included.

The configured local date is the report key in `runtime_runs`; completed reports survive
scheduler restarts. A PostgreSQL transaction advisory lock prevents overlapping schedulers
from sending simultaneously. Each successful message chunk is checkpointed, so normal
retries resume with the remaining chunks. Failed generation or delivery is recorded, with
up to `DAILY_RESEARCH_REPORT_MAX_ATTEMPTS` (default `3`) attempts and a minimum
`DAILY_RESEARCH_REPORT_RETRY_SECONDS` (default `300`) delay. A crash between Telegram accepting
a message and its database checkpoint can repeat that chunk; Telegram delivery and database
writes are not atomic. Missed previous days are not replayed. Operator alerts are separate:
set `RUNTIME_ALERT_TELEGRAM_CHAT_ID` to a nonzero chat ID to enable them; `/alerts` lists alerts.

## Configuration

`src/news_agent/settings.py` is the complete settings reference; `.env.example` contains common overrides. The main controls are:

- Credentials: `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `OPENAI_API_KEY`, optional provider API keys.
- Models and limits: `OPENAI_MODEL`, `MAIN_AGENT_MODEL`, `MAIN_AGENT_MAX_TOOL_ITERATIONS`, `GENERAL_SEARCH_*`, `RESEARCH_WEB_*`, `ANSWER_REFLECTION_*`.
- Sources and scheduling: `DEFAULT_SOURCE_PACK_ENABLED`, `DEFAULT_SOURCES_JSON`, market universe, pipeline intervals, provider timeouts/retries, and per-source config.
- Research: market-impact rules, entity/theme aliases, score weights, evidence gates, source-health threshold, and retention.
- Memory and operations: short-term window/expiry, consolidation batch/retries, runtime retention, report delivery, and alert destination.

Do not confuse live-service fallback output with a successful network call. Missing credentials and provider failures can yield limited deterministic answers; runtime traces and evaluation metadata distinguish those cases.

## Maintenance

Run from the repository root with the virtualenv activated:

```bash
# Repair recoverable article/source links in historical signal snapshots.
news-agent-backfill-evidence --limit 500

# Probe source-pack feeds over the network; no write unless --write is supplied.
news-agent-validate-sources
```

The following commands delete database rows; verify the target database and back up needed data first:

```bash
# Delete generated research/runtime data, preserving users, sources, and memory.
news-agent-reset-data --scope generated

# Delete all application data while retaining the schema.
news-agent-reset-data --scope all
```

Deployment-specific instructions remain in [the CI/CD guide](ci-cd.md). Root design/planning documents are historical or proposed work; executable code and these current-operation references define implemented behavior.
