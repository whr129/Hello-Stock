# Development Commands

- `source .venv/bin/activate` activates the local virtualenv.
- `pip install -e ".[dev]"` installs runtime and dev dependencies.
- `docker compose up -d` starts local services such as Postgres.
- `PYTHONPATH=src .venv/bin/alembic upgrade head` applies database migrations.
- `PYTHONPATH=src .venv/bin/news-agent` runs the Telegram bot.
- `PYTHONPATH=src .venv/bin/news-agent-scheduler` runs the scheduler loop.
- `PYTHONPATH=src .venv/bin/pytest` runs the full test suite.
- `PYTHONPATH=src .venv/bin/ruff check .` runs lint checks.

