# Hello Stock

A Telegram assistant for evidence-backed market research, general web questions, source administration, and runtime inspection. It runs on Python 3.11+, LangGraph, OpenAI, and PostgreSQL with pgvector.

## Functionality

- Research tickers and themes, rank market-attention candidates, and explain signals with source links, score components, and evidence gaps.
- Answer general questions through a tool-calling main agent and cited web search.
- Manage RSS, feed-backed Twitter/newsletter sources, and optional market-news API providers.
- Refresh stored articles, summaries, embeddings, market snapshots, and signals on a schedule or on demand.
- Inspect refresh reports, traces, errors, and alerts from Telegram.
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
