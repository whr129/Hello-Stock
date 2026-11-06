# Market research

The assistant ranks market attention and explains evidence; it does not recommend trades. General web questions, source administration, runtime inspection, and memory are also supported. See [README](../../README.md) for the full command list and [architecture](../architecture.md) for execution flow.

## Research behavior

- `/research` updates research analysis from stored content and returns candidates. It is not a substitute for `/refresh all` when ingestion has not run.
- `/candidates` lists ranked candidates; the planner defaults to at most three.
- `/signals <ticker>` explains a ticker's stored signal, components, evidence, gaps, and score movement.
- `/researchstatus` shows recent research/refresh runs and source availability.
- `/sourcehealth` reports source quality and failures.
- The scheduler delivers a configurable daily report of recent stored candidates to Telegram;
  see [daily report settings](../../README.md#daily-research-report). Refresh diagnostics are
  available on demand rather than pushed after every refresh.

Scores combine mention velocity, diversity, recency, semantic similarity, price momentum, volume, theme persistence, and trust. Weights and evidence gates live in `Settings`. Missing evidence should yield an explicit gap or no-candidate response, never invented links or catalysts. Optional company web research (`RESEARCH_WEB_ENABLED`) supplements stored reports with current external evidence.

## Sources

The curated [default source pack](default-sources.json) is enabled by default. Set `DEFAULT_SOURCE_PACK_ENABLED=false` to disable it or supply `DEFAULT_SOURCES_JSON` to override it. `/sourcepack [category]` lists available starter sources.

| Provider | Configuration |
| --- | --- |
| `rss` | Feed URL |
| `twitter` | Feed-backed account; requires `config.feed_url`, not a native X API integration |
| `newsletter` | Feed-backed account; requires `config.feed_url` |
| `alpha_vantage`, `finnhub`, `polygon` | Corresponding API key in settings |

For example:

```text
/addsource twitter @openai
/sourceconfig 12 feed_url https://example.com/openai-feed.xml
/sourcetest 12
```

Replace the example URL and returned source ID with real values. Account feeds depend on the configured feed service; adding an account alone does not enable native API access.

Per-source `pipeline_tier`, `fetch_interval_seconds`, `max_items`, and `max_item_age_hours` control source polling. Ingestion keeps likely market-impact content using configured categories, keywords, reject terms, and a confidence threshold. Optional LLM classification of uncertain content is disabled by default.

Source health combines freshness, successful fetches, accepted/saved article volume, and link availability. Low-health sources can be skipped; `SOURCE_HEALTH_MIN_SCORE` controls the gate and `source_health_override=true` bypasses it for a specific source. Diagnose failures with `/sourcetest`, `/sourcehealth`, `/refreshreport`, and `/trace` before adjusting gates.

## Quality contracts

[Evidence grounding](evidence-grounding.md) defines attribution and confidence behavior. [Evaluation](evaluation.md) describes the versioned prompt corpus, offline contract tests, and live comparison procedure. General news briefs, watchlists, standalone `/stocks` requests, daily recaps, and topic/local personalization are retired surfaces.

## News and sentiment alerts

Optional market alerts add Jev decisions to stored news and provider sentiment evidence through
OpenRouter: immediate alert, daily-report context, or ignore. They remain informational and
preserve evidence links and uncertainty. Missing or stale timestamps cannot establish urgency;
sentiment changes require enough observations and independent sources. This does not enable
trading or revive retired watchlist/quote commands.

Alerts start disabled, with shadow mode enabled and a $5 monthly Jev assessment budget.
Immediate Telegram delivery reuses the existing bot and daily-report destination (with the
most recent known chat as fallback); `MARKET_ALERT_CHAT_ID` is an optional override. Daily reports keep the configured
18:00 America/Toronto default. See [setup and operations](../../README.md#market-news-and-sentiment-alerts)
for migration, credential checks, source polling, limits, and confirmed-401 quarantine.
