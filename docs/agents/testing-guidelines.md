# Testing Guidelines

Tests use `pytest` and `pytest-asyncio`. Name files `test_*.py` and keep tests focused on one behavior each.

Add regression coverage for router, supervisor, scheduler, provider, runtime-observability, reflection, and memory-pipeline changes before merging.

Run targeted tests during development, then finish with:

```bash
PYTHONPATH=src .venv/bin/pytest
```

