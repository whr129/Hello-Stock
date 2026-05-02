# Hello Stock

Telegram assistant for personalized news, stock snapshots, technical analysis, and general web questions. The project uses LangGraph for orchestration, Postgres + pgvector for storage and retrieval, and OpenAI for routing, summarization, embeddings, and web search.

## What It Does

- Routes Telegram messages through a LangGraph supervisor.
- Uses two domain subagents:
  - `news_agent` for briefs, topics, local preferences, source management, recap settings, and memory tools.
  - `market_agent` for watchlists, live quotes, and lightweight technical analysis.
- Answers off-domain or stale-data queries with OpenAI web search.
- Runs a scheduler that refreshes source content, market snapshots, summaries, daily recaps, and retention cleanup.

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
    router --> search[General web search]
    news --> merge[merge outputs]
    market --> merge
    search --> merge
    merge --> guardrails[financial guardrails]
    guardrails --> memory[persist short/long-term memory]
    memory --> reply[Telegram reply]
```

### Scheduler Flow

```mermaid
flowchart TD
    tick[Scheduler tick loop] --> refresh{refresh due?}
    refresh -->|yes| fetch[fetch sources + market snapshots]
    fetch --> store[dedupe and persist]
    store --> summarize[precompute summaries + embeddings]
    summarize --> recap[send due daily recaps]
    recap --> cleanup[retention cleanup]
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
