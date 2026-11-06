# Hello Stock

A Telegram assistant for evidence-backed market research, general web questions, source administration, and runtime inspection. It runs on Python 3.11+, LangGraph, OpenAI, and PostgreSQL with pgvector.

## Functionality

- Research tickers and themes, rank market-attention candidates, and explain signals with source links, score components, and evidence gaps.
- Answer general questions through a tool-calling main agent and cited web search.
- Manage RSS, feed-backed Twitter/newsletter sources, and optional market-news API providers.
- Refresh stored articles, summaries, embeddings, market snapshots, and signals on a schedule or on demand.
- Inspect refresh reports, traces, errors, and alerts from Telegram.
- Receive a configurable daily research report with ranked candidates and evidence links.
- Keep per-chat conversation context and consolidate durable user memories asynchronously.

The product does not provide investment recommendations, watchlists, general news briefs, or daily recaps.

## Architecture

Two processes share the database: the Telegram bot handles requests; the scheduler refreshes research data and processes memory jobs.

```mermaid
flowchart TD
    Telegram --> Context[Load chat context and semantic memory]
    Context --> Dispatch{Known command?}
    Dispatch -->|yes| Domain[Research / source administration / runtime]
    Dispatch -->|no| Main[Main agent tool loop]
    Main --> Tools[Research / runtime / sources / memory / web search]
    Domain --> Checks[Guardrails and answer reflection]
    Tools --> Checks
    Checks --> Persist[Persist transcript, session and memory jobs]
    Persist --> Reply[Telegram reply]
    Scheduler --> Refresh[Fetch, filter, deduplicate, summarize and score]
    Refresh --> Database[(Postgres + pgvector)]
    Database --> Context
```

See [architecture and operations](docs/architecture.md) for module boundaries, refresh stages, and configuration.

## Local setup

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Set `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, and `DATABASE_URL` in `.env`. Host-run Python processes need a reachable PostgreSQL database with pgvector. For example, a development database using `news_agent` credentials and listening on port `5433` would use:

```dotenv
DATABASE_URL=postgresql+asyncpg://news_agent:news_agent@localhost:5433/news_agent
```

The deployment Compose stack keeps PostgreSQL on an internal Docker network; it does not publish a host port. See the [deployment guide](docs/ci-cd.md) for container setup. For host-run development, configure your database first, then apply migrations:

```bash
alembic upgrade head
```

Run the two processes in separate activated terminals:

```bash
news-agent
news-agent-scheduler
```

Use `/refresh all` to populate research data, then `/research` or `/signals NVDA`. Source failures and insufficient evidence can leave candidates empty; inspect `/sourcehealth` and `/refreshreport`.

## Daily research report

Refreshes run quietly; their diagnostic reports remain available with `/refreshreport`.
The scheduler sends a daily research report at 18:00 America/Toronto by default. Configure it
in `.env`, then restart `news-agent-scheduler`:

```dotenv
DAILY_RESEARCH_REPORT_ENABLED=true
DAILY_RESEARCH_REPORT_TIME=18:00
DAILY_RESEARCH_REPORT_TIMEZONE=America/Toronto
DAILY_RESEARCH_REPORT_CHAT_ID=0
DAILY_RESEARCH_REPORT_MAX_CANDIDATES=3
```

Use a 24-hour `HH:MM` time and an IANA timezone. Set a Telegram chat/group ID for a fixed
destination; `0` uses the most recent user chat when the day's report starts. The bot must
already have access to that chat. Set `DAILY_RESEARCH_REPORT_ENABLED=false` to disable it.

Reports use stored 24-hour signals updated within the past day, with the existing evidence
gates, links, caveats, and an explicit no-candidates message when evidence is insufficient.
Delivery runs on the first scheduler tick after the configured time, including after a
same-day restart; missed previous days are not replayed. The schedule follows local daylight
saving time. Completed reports and sent chunks are persisted, with up to
`DAILY_RESEARCH_REPORT_MAX_ATTEMPTS=3` attempts spaced by at least
`DAILY_RESEARCH_REPORT_RETRY_SECONDS=300`. Delivery status appears in `/runtime` and `/job`.
The old `REFRESH_REPORT_ENABLED` setting is ignored. Operator error alerts remain controlled
separately by `RUNTIME_ALERT_TELEGRAM_CHAT_ID`.

## Market news and sentiment alerts

Jev, accessed through OpenRouter, evaluates collected evidence and chooses `immediate_alert`,
`daily_report`, or `ignore`. It is a decision model, not a news feed. Existing providers supply
news and sentiment observations. Alerts preserve links, uncertainty, and the research-only boundary.

Apply `alembic upgrade head` before starting the scheduler; migration `0007_market_alerts` adds
article provider metadata and durable events, decisions, budget reservations, sentiment snapshots,
and delivery records. Configure `.env`:

```dotenv
OPENROUTER_API_KEY=
MARKET_ALERTS_ENABLED=false
MARKET_ALERT_SHADOW_MODE=true
MARKET_ALERT_CHAT_ID=0
JEV_MODEL=typesafe/jev-1.14
JEV_MONTHLY_BUDGET_USD=5
JEV_REQUEST_RESERVE_USD=0.025
```

Set a valid key and enable alerts to begin assessment. Keep shadow mode enabled while reviewing
results; shadow assessment still spends inference budget but sends no alert messages. Disable shadow mode to deliver alerts using the existing Telegram bot token and daily-report
destination. `MARKET_ALERT_CHAT_ID=0` reuses `DAILY_RESEARCH_REPORT_CHAT_ID`, then the most
recent known chat when that setting is also zero; set a nonzero alert chat ID only to override it. Daily research delivery remains independently configured at
18:00 America/Toronto by default and includes available alert/sentiment context.

The fast collector polls globally owned breaking sources every `MARKET_ALERT_POLL_SECONDS=60`
seconds, subject to each source's `alert_poll_seconds` and provider interval limits. A source can
opt in with `alert_enabled=true`, opt out with `false`, and impose a
`provider_min_interval_seconds` floor. Paid providers also retain `fetch_interval_seconds`.
Collection uses separate alert timestamps and leaves embeddings and summaries to normal refresh.
Missing publication timestamps remain missing; they do not establish urgent freshness.

Delivery defaults include a 30-minute freshness limit, 0.70 minimum confidence, 60-minute cooldown,
and five alerts per hour. Configure `MARKET_ALERT_MAX_AGE_MINUTES`, `MARKET_ALERT_MIN_CONFIDENCE`,
`MARKET_ALERT_COOLDOWN_MINUTES`, and `MARKET_ALERT_MAX_PER_HOUR`. Optional quiet hours use
`MARKET_ALERT_QUIET_START`, `MARKET_ALERT_QUIET_END` (`HH:MM`) and `MARKET_ALERT_TIMEZONE`;
`MARKET_ALERT_CRITICAL_OVERRIDE=true` permits qualifying critical alerts through quiet hours.
Monthly Jev reservations are shared across workers. Budget exhaustion stops paid assessment;
unknown request costs conservatively consume the reservation. This limit covers Jev assessment,
not other providers or the existing research model calls.

Operator commands:

```bash
news-agent-alerts check-credentials
news-agent-alerts inspect
news-agent-alerts once
news-agent-alerts cleanup-unauthorized
news-agent-alerts cleanup-unauthorized --write
```

The credential check uses OpenRouter's [current-key endpoint](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key)
and makes no paid model request. It prints only a safe status, never the token or raw response.
A successful check verifies authentication, not model access. Environment variables take precedence
over `.env`; after rotating a key, update any exported value and restart bot/scheduler processes.
Use `OPENROUTER_API_KEY`, not `OPENROUTER_API`.

`once` requires alerts enabled and follows the same shadow and delivery policies as the scheduler.
`inspect` shows counts/statuses without article text or Telegram payloads. Source cleanup defaults
to a dry run; `--write` disables only persisted, confirmed HTTP 401 sources and preserves article
history. Repeated typed source 401s are quarantined automatically. A Jev/OpenRouter 401 instead
requires credential repair; deleting news sources cannot fix an expired model key. A Telegram send
with an uncertain outcome is marked `unknown` and is not automatically replayed, avoiding duplicates.

## Telegram commands

| Area | Commands |
| --- | --- |
| Research | `/research`, `/candidates`, `/signals <ticker>`, `/researchstatus`, `/sourcehealth` |
| Sources | `/sources`, `/addsource <provider> <target>`, `/sourceconfig <id> <key> <value>`, `/sourcefields <id> <field> <value>`, `/sourcetest <id>`, `/sourcepack [category]`, `/removesource <id>` |
| Refresh | `/refresh [market_prices\|breaking_resources\|daily_resources\|all]` |
| Runtime | `/runtime`, `/job <run-id>`, `/refreshreport [run-id]`, `/trace <run-id>`, `/step <run-id> <step-name>`, `/alerts` |
| Memory | `/memory`, `/forget <memory-id>`, `/resetmemory` |
| Help | `/start`, `/help`, `/skills`, `/resources` |

Natural-language questions use the main agent, for example `research Nvidia and today's AI capex news` or `what happened in the last refresh?`. Use explicit commands for precise source and memory mutations.

## Development and evaluation

```bash
PYTHONPATH=src .venv/bin/pytest
PYTHONPATH=src .venv/bin/ruff check .
```

Tests are separated into [unit, regression, and evaluation suites](tests/README.md). Live answer evaluation is a separate command that needs a migrated, populated database and can call paid APIs and write conversation/runtime data:

```bash
news-agent-eval --cases tests/evaluation/market_research_cases.jsonl
```

See [evaluation guidance](docs/market-research/evaluation.md) for isolated database use and interpreting deterministic versus live judge results.

## Documentation

- [Repository contributor instructions](AGENTS.md)
- [Architecture, configuration, and maintenance](docs/architecture.md)
- [Research behavior and sources](docs/market-research/index.md)
- [Evidence contract](docs/market-research/evidence-grounding.md)
- [Existing CI/CD and deployment guide](docs/ci-cd.md)
