# Market Research Overview

This is the current product surface for the Telegram assistant. The bot is a market research assistant, not a general news brief or watchlist product.

## Kept Surfaces

- `/research`, `/candidates`, `/signals <ticker>`, and `/researchstatus`.
- `/sources`, `/addsource`, `/sourceconfig`, `/sourcefields`, `/sourcetest`, `/sourcepack`, and `/removesource`.
- `/refresh [pipeline]` for manual scheduler runs, including `market_prices`, `breaking_resources`, `daily_resources`, and `all`.
- `/runtime`, `/job`, `/refreshreport`, `/trace`, `/step`, and `/alerts`.
- `/memory`, `/forget`, `/resetmemory`, `/resources`, `/help`, and `/skills`.
- General web search as external context for broad factual questions or missing market background.

## Removed Surfaces

- General news briefs and daily recap delivery.
- Watchlists.
- Standalone `/stocks` quote and technical-analysis requests.
- Topic and local personalization.
- Timezone and recap settings.

## Current Architecture

- `app/supervisor.py` routes chat requests through LangGraph.
- `research/` handles deterministic planning, mention extraction, scoring, analysis, and reporting.
- `domains/news/` is now source, refresh, help/skills, resource inventory, and memory administration.
- `domains/runtime/` handles runtime inspection and alerts.
- `search/` handles general web-search answers outside market research.
- `graph/scheduler_graph.py` runs tiered source fetches, market snapshot refresh, normalization, embeddings, summaries, mention extraction, scoring, and cleanup.
- `memory/` keeps 30-day short-term session state and async long-term memory consolidation.
- `storage/` keeps market entities, mentions, signal snapshots, theme memories, source/article/summary/embedding data, runtime records, jobs, and memories.
- Refresh observability, reporting, and retry behavior are described in [Refresh Observability and Retry Refactor](refresh-observability-refactor.md).
- Research answer grounding and source-link rules are described in [Evidence Grounding](evidence-grounding.md).

## Ingestion Sources

The ingestor stores only items classified as likely to affect public stocks, equity sectors, rates, macro expectations, policy, regulation, earnings, filings, M&A, sanctions, tariffs, or market liquidity. Configure the deterministic gate with `MARKET_IMPACT_ALLOWED_CATEGORIES`, `MARKET_IMPACT_KEYWORDS`, `MARKET_IMPACT_REJECT_TERMS`, and `MARKET_IMPACT_MINIMUM_CONFIDENCE`. Optional LLM classification for uncertain items is disabled by default and controlled by `LLM_MARKET_IMPACT_CLASSIFICATION_ENABLED` and `LLM_MARKET_IMPACT_CLASSIFICATION_THRESHOLD`.

Pulling is source-configurable. Use `pipeline_tier`, `fetch_interval_seconds`, `max_items`, and `max_item_age_hours` in source config to control `breaking_resources` and `daily_resources` behavior. Market prices run through the `market_prices` pipeline every 10 minutes during regular US market hours.

The curated starter pack is checked in at [default-sources.json](default-sources.json) and is enabled by default. It prioritizes official macro, filings, policy, regulatory, energy, commodities, company-specific EDGAR, and market-news feeds. Use `/sourcepack [category]` to list feeds that can be checked or added. Set `DEFAULT_SOURCE_PACK_ENABLED=false` to disable the checked-in pack, or set `DEFAULT_SOURCES_JSON` to override it.

Supported source providers:

- `rss`: requires a feed URL. Useful examples include company IR feeds, SEC press releases, Federal Reserve press releases, and market/news RSS feeds.
- `twitter`: means feed-backed X.com ingestion. Add the account with `/addsource twitter @account`, then set `/sourceconfig <id> feed_url <rss-or-bridge-url>`. Use a bridge you control, such as self-hosted RSSHub or Nitter-style feeds; public bridges are fragile and may be incomplete, delayed, rate-limited, or unavailable.
- `newsletter`: requires `config.feed_url`. Use feed-backed newsletters from Substack, beehiiv, or custom RSS.
- `alpha_vantage`, `finnhub`, and `polygon`: optional API-backed providers enabled only when their API keys are configured.

Regulatory and macro/policy source examples include SEC RSS feeds for 8-K, 10-Q, 10-K, S-1, and insider transactions, plus Fed, Treasury, BLS, BEA, White House, Congress, and regulatory agency feeds.

Do not use the official X API for the current free/low-cost path. Official X API access is the reliable paid option and current X documentation describes pay-per-use API credits and usage monitoring/spending limits: [X API pricing](https://docs.x.com/x-api/getting-started/pricing) and [X usage and billing](https://docs.x.com/x-api/fundamentals/post-cap). A future `x_api` provider can add bearer-token auth, usage budgets, caching, and spending alerts behind the provider boundary.

## Non-Negotiables

- Do not provide buy, sell, or personalized investment advice.
- Keep scoring weights, retention, alert thresholds, provider behavior, and source choices configuration-driven.
- Treat transcripts, learned memories, Telegram data, source credentials, and market research data as sensitive.
- Prefer deterministic behavior for planning, extraction, scoring, and routing. Use LLMs for summarization/explanation where appropriate, with deterministic fallbacks.

## Evaluation

Use [Market Research Evaluation](evaluation.md) when judging answer quality or improving research usefulness.
