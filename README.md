# Hello Stock

Telegram assistant for personalized news, stock snapshots, technical analysis, runtime debugging, and general web questions. The project uses a LangGraph supervisor, Postgres + pgvector, and OpenAI for routing, summarization, embeddings, web search, and memory consolidation.

## What It Does

- Routes Telegram messages through a LangGraph supervisor.
- Uses three subagents:
  - `news_agent` for briefs, topics, local preferences, source management, recap settings, and memory tools.
  - `market_agent` for watchlists, live quotes, and lightweight technical analysis.
  - `runtime_agent` for refresh inspection, trace lookup, error review, alert summaries, and calling-history debugging.
- Uses LangGraph-style short-term memory per chat thread and an async long-term memory pipeline backed by a vector store.
- Answers off-domain or stale-data queries with OpenAI web search.
- Runs a scheduler that refreshes source content, market snapshots, summaries, daily recaps, long-term memory jobs, runtime traces, alerts, and retention cleanup.

## Stack

- Python 3.11+
- `python-telegram-bot`
- LangGraph
- Postgres + pgvector
- SQLAlchemy + Alembic
- OpenAI API
- `feedparser`, `trafilatura`, `yfinance`, `pandas`

## Architecture

### Chat Flow

```mermaid
flowchart TD
    telegram[Telegram update] --> supervisor[LangGraph supervisor]
    supervisor --> router[LLM intent router]
    router --> news[News subagent]
    router --> market[Market subagent]
    router --> runtime[Runtime subagent]
    router --> search[General web search]
    news --> merge[merge outputs]
    market --> merge
    runtime --> merge
    search --> merge
    merge --> guardrails[financial guardrails]
    guardrails --> persist[persist transcript + memory + runtime state]
    persist --> reply[Telegram reply]
```

### Scheduler Flow

```mermaid
flowchart TD
    tick[Scheduler tick loop] --> refresh{refresh due?}
    refresh -->|yes| fetch[fetch sources + market snapshots]
    fetch --> store[dedupe and persist]
    store --> summarize[precompute summaries + embeddings]
    summarize --> memory[memory consolidation jobs]
    memory --> trace[persist run + step traces]
    trace --> recap[send due daily recaps]
    recap --> alerts[push runtime alerts]
    alerts --> cleanup[retention cleanup]
```

## Setup

```bash
cp .env.example .env
docker compose up -d
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src .venv/bin/alembic upgrade head
```

Required `.env` values:

```bash
TELEGRAM_BOT_TOKEN=
DATABASE_URL=postgresql+asyncpg://news_agent:news_agent@localhost:5432/news_agent
OPENAI_API_KEY=
```

Useful optional memory settings:

```bash
SHORT_TERM_MEMORY_WINDOW_SIZE=20
SHORT_TERM_MEMORY_EXPIRY_MINUTES=60
LONG_TERM_MEMORY_BATCH_SIZE=20
LONG_TERM_MEMORY_TOP_K=5
MEMORY_CANDIDATES_PER_BATCH=6
MEMORY_JOB_MAX_RETRIES=3
```

## Run

Start the bot:

```bash
PYTHONPATH=src .venv/bin/news-agent
```

Start the scheduler in a second terminal:

```bash
PYTHONPATH=src .venv/bin/news-agent-scheduler
```

Run tests:

```bash
PYTHONPATH=src .venv/bin/pytest
PYTHONPATH=src .venv/bin/ruff check .
```

## Architecture Notes

- `src/news_agent/app/supervisor.py` is the main LangGraph entrypoint.
- `src/news_agent/domains/news/`, `domains/market/`, and `domains/runtime/` hold the subagents.
- `src/news_agent/memory/` contains the short-term message-state helpers and the async long-term memory consolidation service.
- `src/news_agent/observability/` records runtime runs, ordered step traces, errors, and alert deliveries.
- `src/news_agent/graph/chat_graph.py` remains a compatibility entrypoint that delegates to the supervisor graph.

## Telegram Commands

- `/brief`, `/stocks <ticker...>`, `/watch <ticker...>`, `/unwatch <ticker...>`
- `/topics <topic...>`, `/local <region>`
- `/sources`, `/addsource <provider> <target>`, `/sourceconfig <id> <key> <value>`, `/sourcefields <id> <field> <value>`, `/sourcetest <id>`, `/removesource <id>`
- `/refresh`
- `/runtime`, `/job <run-id>`, `/trace <run-id>`, `/step <run-id> <step-name>`, `/alerts`
- `/timezone <Area/City>`, `/recaptime <HH:MM>`, `/recapoff`, `/recapstatus`
- `/memory`, `/forget <memory-id>`, `/resetmemory`
- `/skills`, `/help`

You can also ask natural-language questions directly, for example:
- `what's google performance today`
- `brief me on nvidia and today's ai news`
- `who won the world series last year?`
- `what happened in the last refresh?`
- `what was the error in the last refresh?`

## Source Providers

Supported source types are `rss`, `twitter`, and `newsletter`.

- `rss` works directly with a feed URL.
- `twitter` and `newsletter` are currently feed-backed account sources, not native API integrations.
- For `twitter` or `newsletter`, you usually need `config.feed_url` after `/addsource`.

Example:

```text
/addsource twitter @openai
/sourceconfig 12 feed_url https://example.com/openai-feed.xml
/sourcetest 12
```

## Safety

Stock output is informational only. The market path can summarize price movement and indicators, but it should not provide buy/sell recommendations.

## Runtime Alerts

Set `RUNTIME_ALERT_TELEGRAM_CHAT_ID` to a Telegram chat id if you want operator-facing runtime alerts for failed or completed-with-errors runs.

## Runtime History

The runtime layer records:
- run headers for chat requests, scheduled refreshes, manual refreshes, and daily recaps
- ordered step traces for supervisor nodes, subagent calls, provider fetches, and tool-like operations
- normalized runtime errors linked to a run and step

Use `/runtime` for the latest summary, `/job` for one run, `/trace` for the ordered call sequence, and `/step` to inspect one refresh or provider step during debugging.

## Memory System

Short-term memory is maintained per chat thread as LangGraph-style message state and persisted with a rolling window. `/memory` shows the recent thread context from that state.

Long-term memory is no longer stored inline on every message. The bot writes a conversation transcript, and the scheduler runs an async memory job after every 20 new user messages for a user. That job:
- extracts durable atomic memory candidates with an LLM
- compares them against existing vector memories
- decides whether to add, update, or skip each candidate

Long-term memory retrieval is semantic and vector-backed rather than “latest rows only.”
