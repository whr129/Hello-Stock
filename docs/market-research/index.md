# Market Research Overview

This is the current product surface for the Telegram assistant. The bot is a market research assistant, not a general news brief or watchlist product.

## Kept Surfaces

- `/research`, `/candidates`, `/signals <ticker>`, and `/researchstatus`.
- `/stocks <ticker...>` for explicit market snapshots and technical context.
- `/sources`, `/addsource`, `/sourceconfig`, `/sourcefields`, `/sourcetest`, and `/removesource`.
- `/refresh` for manual scheduler runs.
- `/runtime`, `/job`, `/trace`, `/step`, and `/alerts`.
- `/memory`, `/forget`, `/resetmemory`, `/help`, and `/skills`.
- General web search as external context for broad factual questions or missing market background.

## Removed Surfaces

- General news briefs and daily recap delivery.
- Watchlists.
- Topic and local personalization.
- Timezone and recap settings.

## Current Architecture

- `app/supervisor.py` routes chat requests through LangGraph.
- `research/` handles deterministic planning, mention extraction, scoring, analysis, and reporting.
- `domains/market/` handles explicit `/stocks` requests.
- `domains/news/` is now source, refresh, help/skills, and memory administration.
- `domains/runtime/` handles runtime inspection and alerts.
- `graph/scheduler_graph.py` runs source fetch, market snapshot refresh, normalization, embeddings, summaries, mention extraction, scoring, and cleanup.
- `memory/` keeps 30-day short-term session state and async long-term memory consolidation.
- `storage/` keeps market entities, mentions, signal snapshots, theme memories, source/article/summary/embedding data, runtime records, jobs, and memories.

## Non-Negotiables

- Do not provide buy, sell, or personalized investment advice.
- Keep scoring weights, retention, alert thresholds, provider behavior, and source choices configuration-driven.
- Treat transcripts, learned memories, Telegram data, source credentials, and market research data as sensitive.
- Prefer deterministic behavior for planning, extraction, scoring, and routing. Use LLMs for summarization/explanation where appropriate, with deterministic fallbacks.

## Evaluation

Use [Market Research Evaluation](evaluation.md) when judging answer quality or improving research usefulness.
