# Coding Style

Use Python 3.11+ with 4-space indentation and type hints for new code. Follow Ruff defaults configured in `pyproject.toml`; line length is `100`.

Prefer `snake_case` for functions, variables, and modules, `PascalCase` for classes, and explicit domain names such as `RuntimeTraceService`, `MemoryConsolidationService`, or `ResearchSubagent`.

Keep routing, source behavior, runtime alerts, and memory batching configuration-driven. Avoid hardcoded provider, ticker, or memory-rule logic.
