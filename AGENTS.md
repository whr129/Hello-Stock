# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `src/news_agent/`. Key areas:
- `app/` contains the LangGraph supervisor and shared state.
- `domains/news/`, `domains/market/`, and `domains/runtime/` hold the three subagents.
- `agent/` contains routing, guardrails, and response-building helpers.
- `scheduler/`, `graph/`, and `ingestion/` cover scheduled refreshes, graph nodes, and source fetching.
- `observability/` stores runtime tracing and alert logic.
- `storage/` contains SQLAlchemy models and repositories for app, scheduler, and runtime records.
- `bot/` contains the Telegram entrypoint.

Database migrations live in `migrations/versions/`. Tests live in `tests/` and mirror behavior by feature, for example `tests/test_router.py`, `tests/test_scheduler_service.py`, and `tests/test_runtime_subagent.py`.

## Build, Test, and Development Commands
- `source .venv/bin/activate` activates the local virtualenv.
- `pip install -e ".[dev]"` installs runtime and dev dependencies.
- `docker compose up -d` starts local services such as Postgres.
- `PYTHONPATH=src .venv/bin/alembic upgrade head` applies database migrations.
- `PYTHONPATH=src .venv/bin/news-agent` runs the Telegram bot.
- `PYTHONPATH=src .venv/bin/news-agent-scheduler` runs the scheduler loop.
- `PYTHONPATH=src .venv/bin/pytest` runs the full test suite.
- `PYTHONPATH=src .venv/bin/ruff check .` runs lint checks.

## Coding Style & Naming Conventions
Use Python 3.11+ with 4-space indentation and type hints for new code. Follow Ruff defaults configured in `pyproject.toml`; line length is `100`. Prefer `snake_case` for functions, variables, and modules, `PascalCase` for classes, and explicit domain names such as `RuntimeTraceService` or `MarketSubagent`. Keep routing and source behavior configuration-driven; avoid hardcoded provider or ticker logic.

## Testing Guidelines
Tests use `pytest` and `pytest-asyncio`. Name files `test_*.py` and keep tests focused on one behavior each. Add regression coverage for router, supervisor, scheduler, provider, and runtime-observability changes before merging. Run targeted tests during development, then finish with `PYTHONPATH=src .venv/bin/pytest`.

## Commit & Pull Request Guidelines
Recent history uses short, imperative commits with prefixes like `fix:`. Follow that pattern, for example `feat: add search fallback` or `test: cover source config errors`. PRs should include:
- a short description of the user-visible change
- any migration or `.env` changes
- test evidence, such as `48 passed`
- screenshots or sample bot output when Telegram responses changed

## Security & Configuration Tips
Keep secrets in `.env`, especially `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL`, and `RUNTIME_ALERT_TELEGRAM_CHAT_ID`. Do not commit credentials or production chat data. When adding new providers, router rules, or runtime alerts, prefer configuration-driven behavior over hardcoded source-specific logic.
