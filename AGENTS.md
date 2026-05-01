# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `src/news_agent/`. Key areas:
- `app/` contains the LangGraph supervisor and shared state.
- `domains/news/` and `domains/market/` hold the two main subagents.
- `agent/` contains routing, guardrails, and response-building helpers.
- `scheduler/`, `ingestion/`, and `storage/` cover scheduled refreshes, source fetching, and persistence.
- `bot/` contains the Telegram entrypoint.

Database migrations live in `migrations/versions/`. Tests live in `tests/` and mirror behavior by feature, for example `tests/test_router.py` and `tests/test_scheduler_service.py`.

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
Use Python 3.11+ with 4-space indentation and type hints for new code. Follow Ruff defaults configured in `pyproject.toml`; line length is `100`. Prefer `snake_case` for functions, variables, and modules, `PascalCase` for classes, and explicit, domain-based names such as `MarketSubagent` or `SchedulerControlService`.

## Testing Guidelines
Tests use `pytest` and `pytest-asyncio`. Name files `test_*.py` and keep tests focused on one behavior each. Add regression tests for router, supervisor, scheduler, or provider changes before merging. Run targeted tests during development, then finish with `PYTHONPATH=src .venv/bin/pytest`.

## Commit & Pull Request Guidelines
Recent history uses short, imperative commits with prefixes like `fix:`. Follow that pattern, for example `feat: add search fallback` or `test: cover source config errors`. PRs should include:
- a short description of the user-visible change
- any migration or `.env` changes
- test evidence, such as `48 passed`
- screenshots or sample bot output when Telegram responses changed

## Security & Configuration Tips
Keep secrets in `.env`, especially `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, and `DATABASE_URL`. Do not commit credentials or production chat data. When adding new providers or tools, prefer configuration-driven behavior over hardcoded source-specific logic.
