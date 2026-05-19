# Hello Stock

Telegram assistant for market-impact research, runtime debugging, memory, source management, and general web questions. The project uses a LangGraph supervisor, Postgres + pgvector, and OpenAI for routing, summarization, embeddings, web search, evaluation, and memory consolidation.

## What It Does

- Routes Telegram messages through a LangGraph supervisor.
- Uses focused subagents:
  - `news_agent` for source management, refresh control, skills/help, and memory tools.
  - `research_agent` for market-impact extraction, signal scoring, candidates, and ticker evidence.
  - `runtime_agent` for refresh inspection, trace lookup, error review, alert summaries, and calling-history debugging.
- Uses LangGraph-style short-term memory per chat thread and an async long-term memory pipeline backed by a vector store.
- Answers off-domain or stale-data queries with OpenAI web search.
- Reflects on each candidate answer before persistence so clearly wrong routes can retry once.
- Runs a scheduler that refreshes market-impact source content, market snapshots, summaries, signal scoring, long-term memory jobs, runtime traces, alerts, and retention cleanup.

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
    router --> runtime[Runtime subagent]
    router --> search[General web search]
    news --> merge[merge outputs]
    runtime --> merge
    search --> merge
    merge --> guardrails[financial guardrails]
    guardrails --> reflect[answer reflection]
    reflect -->|retry route once| router
    reflect --> persist[persist transcript + memory + runtime state]
    persist --> reply[Telegram reply]
```

### Scheduler Flow

```mermaid
flowchart TD
    tick[Scheduler tick loop] --> refresh{refresh due?}
    refresh -->|yes| fetch[fetch sources + market snapshots]
    fetch --> store[dedupe and persist]
    store --> summarize[precompute summaries + embeddings]
    summarize --> mentions[extract market mentions]
    mentions --> score[score candidate signals]
    score --> memory[memory consolidation jobs]
    memory --> trace[persist run + step traces]
    trace --> alerts[push runtime alerts]
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
ANSWER_REFLECTION_ENABLED=true
ANSWER_REFLECTION_MAX_RETRIES=1
SHORT_TERM_MEMORY_WINDOW_SIZE=20
SHORT_TERM_MEMORY_EXPIRY_MINUTES=43200
CONVERSATION_EVENT_RETENTION_DAYS=30
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
- `src/news_agent/domains/news/` and `domains/runtime/` hold the command subagents.
- `src/news_agent/research/` contains planner-driven market research extraction, scoring, and reporting.
- `src/news_agent/search/` contains the general search agent.
- `src/news_agent/memory/` contains the short-term message-state helpers and the async long-term memory consolidation service.
- `src/news_agent/observability/` records runtime runs, ordered step traces, errors, and alert deliveries.
- `src/news_agent/graph/chat_graph.py` remains a compatibility entrypoint that delegates to the supervisor graph.

## Telegram Commands

- `/research`, `/candidates`, `/signals <ticker>`, `/researchstatus`
- `/sources`, `/addsource <provider> <target>`, `/sourceconfig <id> <key> <value>`, `/sourcefields <id> <field> <value>`, `/sourcetest <id>`, `/removesource <id>`
- `/refresh`
- `/runtime`, `/job <run-id>`, `/trace <run-id>`, `/step <run-id> <step-name>`, `/alerts`
- `/memory`, `/forget <memory-id>`, `/resetmemory`
- `/skills`, `/help`

You can also ask natural-language questions directly, for example:
- `research nvidia and today's ai capex news`
- `who won the world series last year?`
- `what happened in the last refresh?`
- `what was the error in the last refresh?`

## Source Providers

Supported source types are `rss`, `twitter`, and `newsletter`.

- `rss` works directly with a feed URL.
- `twitter` and `newsletter` are currently feed-backed account sources, not native API integrations.
- For `twitter` or `newsletter`, you usually need `config.feed_url` after `/addsource`.
- No broad default feeds are created unless `DEFAULT_SOURCES_JSON` is configured.

Example:

```text
/addsource twitter @openai
/sourceconfig 12 feed_url https://example.com/openai-feed.xml
/sourcetest 12
```

## Safety

Market research output is informational only. The assistant should not provide buy/sell recommendations.

## Maintenance And Evaluation

Reset generated data while preserving users, sources, and memory:

```bash
PYTHONPATH=src .venv/bin/news-agent-reset-data --scope generated
```

Reset all application data while preserving the schema:

```bash
PYTHONPATH=src .venv/bin/news-agent-reset-data --scope all
```

Run market-research answer evaluation:

```bash
PYTHONPATH=src .venv/bin/news-agent-eval
```

## Runtime Alerts

Set `RUNTIME_ALERT_TELEGRAM_CHAT_ID` to a Telegram chat id if you want operator-facing runtime alerts for failed or completed-with-errors runs.

## Runtime History

The runtime layer records:
- run headers for chat requests, scheduled refreshes, and manual refreshes
- ordered step traces for supervisor nodes, subagent calls, provider fetches, and tool-like operations
- normalized runtime errors linked to a run and step

Use `/runtime` for the latest summary, `/job` for one run, `/trace` for the ordered call sequence, and `/step` to inspect one refresh or provider step during debugging. `/trace <run-id>` includes the run status, timestamps, summary, step metadata, nested child steps when present, and any recorded errors.

## Answer Reflection

After a candidate response passes normal guardrails, the supervisor asks a conservative reflection model whether the selected route and answer match the user request. Reflection can return `pass`, `retry`, or `fail`.

- `pass`: persist and return the answer.
- `retry`: clear prior subagent/search outputs, apply the corrected intent and args, and rerun routing once by default.
- `fail` or exhausted retry limit: return the best available answer with a short user-facing note.

Reflection is controlled by `ANSWER_REFLECTION_ENABLED` and `ANSWER_REFLECTION_MAX_RETRIES`. Reflection decisions are recorded in runtime step metadata, and exhausted reflection marks the chat run as `completed_with_errors`.

## Memory System

Short-term memory is maintained per chat thread as LangGraph-style message state and persisted with a rolling window. `/memory` shows the recent thread context from that state.

Long-term memory is no longer stored inline on every message. The bot writes a conversation transcript, and the scheduler runs an async memory job after every 20 new user messages for a user. That job:
- extracts durable atomic memory candidates with an LLM
- compares them against existing vector memories
- decides whether to add, update, or skip each candidate

Long-term memory retrieval is semantic and vector-backed rather than “latest rows only.”
