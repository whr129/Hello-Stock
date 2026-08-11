# Project Structure

Core application code lives in `src/news_agent/`. Key areas:

- `app/` contains the LangGraph supervisor and shared state.
- `domains/news/`, `domains/runtime/`, `research/`, and `search/` hold the focused subagents and market/general search pipelines.
- `agent/` contains routing, guardrails, reflection, and response-building helpers.
- `memory/` contains short-term message-state helpers, embeddings, and long-term memory consolidation logic.
- `scheduler/`, `graph/`, and `ingestion/` cover scheduled market-impact refreshes, graph nodes, and source fetching.
- `observability/` stores runtime tracing and alert logic.
- `storage/` contains SQLAlchemy models and repositories for app, scheduler, and runtime records.
- `bot/` contains the Telegram entrypoint.

Database migrations live in `migrations/versions/`. Tests live in `tests/` and mirror behavior by feature, for example `tests/test_router.py`, `tests/test_scheduler_service.py`, `tests/test_runtime_subagent.py`, and `tests/test_memory_consolidation.py`.
