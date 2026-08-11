# Repository Guidelines

This file is the entrypoint for humans and coding agents working in this repository. Keep it short and use the linked documents for details.

## Basics
- Core application code lives in `src/news_agent/`.
- Tests live in `tests/` and generally mirror behavior by feature.
- Database migrations live in `migrations/versions/`.
- Use Python 3.11+ with type hints for new code.
- Keep routing, provider behavior, runtime alerts, and memory heuristics configuration-driven.
- Treat transcripts, learned memories, Telegram data, and secrets as sensitive user data.

## Table of Contents
- [Project Structure](docs/agents/project-structure.md)
- [Development Commands](docs/agents/development-commands.md)
- [Coding Style](docs/agents/coding-style.md)
- [Testing Guidelines](docs/agents/testing-guidelines.md)
- [Commit and PR Guidelines](docs/agents/commit-pr-guidelines.md)
- [Security and Configuration](docs/agents/security-configuration.md)
- [Market Research Overview](docs/market-research/index.md)

## Product Notes
- This bot is market-research-only: no general news briefs, watchlists, local/topic preferences, or daily recap features.
- Use [Market Research Overview](docs/market-research/index.md) for the current product surface.
- Use [Market Research Evaluation](docs/market-research/evaluation.md) when judging answer quality or improving research output usefulness.
