# Planner-Driven Market Research Refactor Plan

## 1. Product Vision

The current news agent is useful for personalized news, watchlist snapshots, source management, and runtime inspection. This refactor expands it into an autonomous market research assistant that can continuously monitor news and market context, detect emerging attention and momentum, remember durable market narratives, and explain why a stock or theme is becoming important.

The product should rank likely future attention or momentum candidates. It must not provide buy, sell, or personalized investment advice. Every stock-related output should stay framed as informational research and include evidence, weak-signal caveats, and the existing financial guardrails.

The target experience is a Telegram-first research assistant that can answer questions like:

- "What names are starting to get attention?"
- "Why is MU showing up in the candidates list?"
- "Run deep research on the top market themes."
- "What changed since yesterday's brief?"
- "Which weak signals should I watch next?"

The system should move from fixed request routing to a planner-led workflow. A planner decides the research objective, entities, horizon, specialized agents, and output format. Specialized agents then gather, score, analyze, remember, and report evidence.

## 2. Product Goals

1. Detect emerging market attention before it becomes obvious in simple watchlist snapshots.
2. Combine 30 days of embedded news context with fresh market data and durable learned themes.
3. Explain rankings with component scores and concrete evidence.
4. Support both scheduled autonomous briefs and on-demand research commands.
5. Keep all provider behavior, score weights, thresholds, retention, and source choices configuration-driven.
6. Preserve privacy and safety for transcripts, learned memories, Telegram data, market preferences, and secrets.

## 3. Non-Goals

1. Do not build an execution or trading recommendation system.
2. Do not guarantee prediction of future price moves.
3. Do not require social data providers in the first release.
4. Do not make PDF output a first-release blocker.
5. Do not replace existing news ingestion, summarization, watchlist, runtime observability, or memory consolidation unless needed for integration.

## 4. Default Product Choices

### Universe

The first version monitors US liquid names first:

- S&P 500
- Nasdaq 100
- User watchlist

The universe should be configurable through `Settings`, source config, or a later database-managed universe table. The first implementation can use a static seed list plus watchlist symbols, then evolve toward a maintained universe source.

### Ranking Objective

The ranking goal is attention plus momentum, not valuation and not investment advice.

The system should favor candidates with:

- Rising mention velocity
- Diverse source coverage
- Recent related news
- Relevant semantic similarity to active market themes
- Positive or unusual price momentum
- Volume anomaly
- Recurring durable themes
- Higher source trust

### Autonomy

The system should send scheduled briefs only when signal thresholds are crossed, while still allowing on-demand commands any time.

## 5. User Experience

### Telegram Commands

Add these commands:

- `/research`
  - Runs a deep market research job now.
  - Uses planner-selected agents based on default market research intent or optional arguments.

- `/candidates`
  - Shows current ranked future-attention candidates.
  - Output includes top candidates, total scores, score components, themes, and concise evidence.

- `/signals <ticker>`
  - Explains why a ticker is ranked.
  - Output includes mention velocity, source diversity, recent articles, market snapshots, related themes, missing evidence, and confidence.

- `/researchstatus`
  - Shows latest scheduled research runs and failures.
  - Reuses the runtime/job observability surface where possible.

### Scheduled Brief Format

Scheduled market research briefs should include:

1. Top 5 candidates.
2. Why each candidate is moving.
3. Score components.
4. Related themes.
5. Key sources and article titles.
6. Price and volume context.
7. Missing or weak evidence.
8. Explicit "not financial advice" guardrail.

Example structure:

```text
Market attention candidates

1. MU - score 78
   Why: memory and AI infrastructure coverage is accelerating across Reuters and MarketWatch.
   Components: mentions 24, diversity 14, recency 10, price momentum 12, volume 8, theme persistence 10.
   Evidence: [source titles...]
   Weakness: filings catalyst not confirmed.

Not financial advice. This is an attention and momentum research ranking.
```

### On-Demand Explanation Format

Ticker-specific signal explanations should answer:

- What is the current rank?
- What changed recently?
- Which sources support the signal?
- Which price or volume context matters?
- Which themes connect this ticker to other candidates?
- What evidence is weak, missing, stale, or contradictory?

## 6. System Architecture

### Current Direction

The current app has clear boundaries:

- Telegram handlers in `src/news_agent/bot/handlers.py`
- Routing in `src/news_agent/agent/router.py`
- Domain subagents under `src/news_agent/domains/`
- Scheduler flow under `src/news_agent/scheduler/` and `src/news_agent/graph/scheduler_graph.py`
- Storage models and repositories under `src/news_agent/storage/`
- Retrieval under `src/news_agent/storage/retrieval.py`
- Market provider and indicators under `src/news_agent/markets/`
- Runtime observability under `src/news_agent/observability/`

The refactor should extend those boundaries rather than create an unrelated pipeline.

### Target Direction

Add a new research package:

```text
src/news_agent/research/
  __init__.py
  planner.py
  schemas.py
  extraction.py
  scoring.py
  retrieval.py
  analysis.py
  reporting.py
  agents.py
  scheduler.py
```

Responsibilities:

- `schemas.py`: structured plan, entities, score components, candidate explanations, report DTOs.
- `planner.py`: planner agent and deterministic fallback planning.
- `extraction.py`: ticker, company, sector, and theme extraction from articles and summaries.
- `scoring.py`: weighted popularity and momentum scoring.
- `retrieval.py`: market-context retrieval wrapper around existing storage retrieval.
- `analysis.py`: candidate explanation and weak-evidence assessment.
- `reporting.py`: Telegram-ready formatting.
- `agents.py`: specialized agent orchestration contracts.
- `scheduler.py`: scheduled job functions for research-specific work.

## 7. Planner Design

### PlannerAgent Purpose

Replace fixed router-only behavior for research flows with a `PlannerAgent` that outputs a structured plan. The router can still detect high-level intent and commands, but research execution should be driven by a plan object.

### Plan Schema

The planner should produce:

- `task_type`
  - `brief`
  - `stock_lookup`
  - `deep_research`
  - `alert_review`
  - `source_admin`

- `entities`
  - tickers
  - companies
  - sectors
  - themes

- `research_horizon`
  - `intraday`
  - `7d`
  - `30d`

- `agents_to_run`
  - `news`
  - `market`
  - `macro`
  - `social`
  - `filings`
  - `memory`
  - `analysis`
  - `report`

- `output_format`
  - `telegram_summary`
  - `long_report`
  - `alert`
  - `pdf_later`

- `constraints`
  - max candidates
  - minimum confidence
  - source families
  - include watchlist
  - include weak evidence

### Deterministic Fallbacks

The planner should have deterministic behavior when no LLM is available:

- `/research`: deep research, 30-day horizon, all configured market agents.
- `/candidates`: brief ranking, 30-day horizon, analysis and report agents.
- `/signals <ticker>`: stock lookup, ticker entity, 30-day horizon, news, market, memory, analysis, report.
- Scheduled threshold alert: alert review, intraday plus 7-day context, analysis and report.

## 8. Specialized Agent Design

### NewsResearchAgent

Responsibilities:

- Retrieve 30-day article and summary context.
- Use pgvector semantic search where embeddings are available.
- Use lexical filters for ticker, company aliases, sectors, and themes.
- Return top articles, summaries, sources, trust scores, and freshness metadata.

### MarketDataAgent

Responsibilities:

- Refresh prices, percent change, volume, volatility, relative strength, and selected indicators.
- Store snapshots using existing `MarketSnapshot` and new signal snapshot tables.
- Provide market context for scoring and explanation.

### FilingsAgent

Responsibilities:

- Check SEC or company IR catalysts when a ticker is high-ranking or explicitly requested.
- First version can be source-config driven through RSS or custom source providers.
- Later versions can add SEC API integration.

### MacroAgent

Responsibilities:

- Add CPI, Fed, Treasury yield, rates, dollar, oil, and sector macro context when relevant.
- Activate when the planner detects macro-heavy themes or market-wide moves.

### SocialTrendAgent

Responsibilities:

- Optional first-release agent.
- Pull Reddit, YouTube, X-feed, or other source-config-driven signals only when credentials or feeds are configured.
- Store source family as `social`.

### MemoryAgent

Responsibilities:

- Retrieve durable market theme memories.
- Store repeated narratives and causal patterns.
- Connect adjacent tickers through learned themes.

Example memory:

```text
AI memory demand is repeatedly linked to MU/NVDA supply chain stories.
```

### AnalysisAgent

Responsibilities:

- Combine retrieved evidence, market snapshots, mentions, and theme memories.
- Rank candidates.
- Explain score components.
- Identify weak or missing evidence.

### ReportAgent

Responsibilities:

- Format Telegram briefs, candidate lists, signal explanations, and longer research reports.
- Apply financial guardrail language consistently.
- Keep source citations concise and readable.

## 9. Data Model

### Existing Retention

Keep:

- `ARTICLE_RETENTION_DAYS = 30`

Change:

- `SNAPSHOT_RETENTION_DAYS` default should become `30`

Add:

- `SIGNAL_RETENTION_DAYS = 30`

### New Tables

Add Alembic migration for these tables.

#### `market_entities`

Stores canonical tickers, company names, sectors, and aliases.

Columns:

- `id`
- `ticker`
- `company_name`
- `sector`
- `industry`
- `aliases`
- `exchange`
- `active`
- `created_at`
- `updated_at`

Constraints:

- Unique ticker where ticker is not null.
- Index ticker, sector, active.

#### `market_mentions`

Stores extracted mentions from articles and summaries.

Columns:

- `id`
- `entity_id`
- `ticker`
- `theme`
- `source_family`
- `source_id`
- `article_id`
- `summary_id`
- `mention_count`
- `sentiment`
- `novelty`
- `trust_score`
- `evidence_text`
- `created_at`

Constraints and indexes:

- Index ticker and theme.
- Index created_at.
- Index source_family.
- Optional uniqueness on article plus ticker/theme to avoid duplicate extraction.

#### `market_signal_snapshots`

Stores explainable popularity and momentum scores.

Columns:

- `id`
- `ticker`
- `theme`
- `window`
- `mention_velocity`
- `source_diversity`
- `recency_score`
- `semantic_similarity`
- `price_momentum`
- `volume_signal`
- `theme_persistence`
- `trust_score`
- `total_score`
- `component_scores`
- `evidence`
- `created_at`

Constraints and indexes:

- Index ticker, theme, created_at.
- Index total_score.
- Index window.

#### `market_theme_memories`

Stores durable learned market narratives.

Columns:

- `id`
- `theme`
- `summary`
- `related_tickers`
- `related_sectors`
- `evidence_count`
- `confidence`
- `first_seen_at`
- `last_seen_at`
- `created_at`
- `updated_at`

Constraints and indexes:

- Index theme.
- Index last_seen_at.
- Optional GIN index on related tickers.

### Repository Layer

Add repository classes:

- `MarketEntityRepository`
- `MarketMentionRepository`
- `MarketSignalRepository`
- `MarketThemeMemoryRepository`

Methods should include:

- Upsert entity by ticker.
- Save extracted mention.
- List unprocessed articles or summaries for mention extraction.
- Aggregate mentions by ticker/theme and window.
- Save signal snapshots.
- Fetch top candidates by latest total score.
- Fetch signal history for a ticker.
- Upsert theme memory by normalized theme.
- Delete records older than `SIGNAL_RETENTION_DAYS`.

## 10. Mention Extraction

### Extraction Sources

Run extraction over:

- Article titles
- Extracted article text when available
- Article summaries
- Existing related ticker fields

### Entity Types

Extract:

- Tickers
- Company aliases
- Sectors
- Themes

### First Implementation

Use deterministic extraction first:

- Cashtags: `$NVDA`
- Uppercase ticker-like tokens already handled by router helpers
- Known aliases from `market_entities`
- Source category and keyword maps for themes

Theme examples:

- AI infrastructure
- memory chips
- cloud capex
- rates
- regional banks
- energy supply
- obesity drugs
- defense spending

### Later Implementation

Add LLM-assisted extraction behind a config flag:

- Return structured JSON.
- Validate against schema.
- Store evidence snippets.
- Keep deterministic fallback.

## 11. Scoring Design

### Component Scores

The total score should be explainable and stored with component values.

Default formula:

```text
total_score =
  mention_velocity_weight * mention_velocity
  + source_diversity_weight * source_diversity
  + recency_weight * recency_score
  + semantic_similarity_weight * semantic_similarity
  + price_momentum_weight * price_momentum
  + volume_weight * volume_signal
  + theme_persistence_weight * theme_persistence
  + trust_weight * trust_score
```

### Settings

Add settings:

- `MARKET_UNIVERSE_DEFAULT`
- `SIGNAL_RETENTION_DAYS = 30`
- `SIGNAL_ALERT_THRESHOLD`
- `SIGNAL_WEIGHT_MENTION_VELOCITY`
- `SIGNAL_WEIGHT_SOURCE_DIVERSITY`
- `SIGNAL_WEIGHT_RECENCY`
- `SIGNAL_WEIGHT_SEMANTIC_SIMILARITY`
- `SIGNAL_WEIGHT_PRICE_MOMENTUM`
- `SIGNAL_WEIGHT_VOLUME`
- `SIGNAL_WEIGHT_THEME_PERSISTENCE`
- `SIGNAL_WEIGHT_TRUST`

### Windows

Compute scores over:

- 1 hour
- 24 hours
- 7 days
- 30 days

The 30-day context gives baseline and persistence. Short windows detect acceleration.

### Mention Velocity

Compare recent mentions against a baseline:

```text
mention_velocity = recent_mentions / max(baseline_mentions_per_window, 1)
```

Normalize to a bounded score, for example 0 to 100.

### Source Diversity

Reward coverage across source families:

- mainstream news
- market news
- filings
- company IR
- macro
- social

Do not reward duplicate articles from the same feed as much as independent coverage.

### Recency

Recent evidence should receive higher weight, with decay over time.

### Semantic Similarity

Use pgvector article and summary embeddings to score similarity to:

- planned themes
- ticker/company query
- known durable market memories

### Price Momentum

Use existing market snapshots and indicators:

- 1-day percent change
- 5-day or 7-day return when available
- relative strength versus index or sector when available

### Volume Signal

Use provider data when available:

- current volume versus average volume
- volume percentile
- abnormal volume flag

If volume data is unavailable, score should remain neutral rather than fail.

### Theme Persistence

Reward repeated narratives seen across days or weeks. Use `market_theme_memories` and 30-day mention history.

### Trust Score

Use source trust from `Source.trust_score` where available. Missing trust should default to neutral.

## 12. Retrieval Design

### New API

Add:

```python
retrieve_market_context(plan: ResearchPlan) -> MarketContext
```

This can live in `src/news_agent/research/retrieval.py` and wrap the existing `RetrievalService`.

### Hybrid Retrieval

Use:

- pgvector semantic search over article embeddings.
- pgvector semantic search over summary embeddings.
- Lexical filters for ticker, company aliases, sector, and theme.
- Date filters capped to 30 days by default.
- Popularity score boost.
- Source trust boost.

### Returned Context

Return:

- Top articles
- Top summaries
- Market mentions
- Signal snapshots
- Market snapshots
- Theme memories
- Score components
- Weak or missing evidence flags

### Future Query Behavior

When the user asks "what may be next", retrieval should include:

- Prior high-scoring stocks and themes.
- Adjacent tickers connected through theme memory.
- Repeated narratives that have not yet produced broad source diversity.

Example:

```text
NVDA demand -> AI servers -> HBM and memory supply -> MU
```

## 13. Scheduler Design

Extend the scheduler into separate scheduled jobs. Each job should emit runtime traces and errors through the existing observability system.

### Jobs

#### `news_refresh`

Existing fetch, dedupe, embed, summarize flow.

#### `market_snapshot_refresh`

Refresh prices, volume, relative strength, volatility, and market snapshots for the configured universe and user watchlists.

#### `mention_extraction`

Extract tickers and themes from new articles and summaries.

#### `popularity_scoring`

Aggregate mention and momentum scores across 1h, 24h, 7d, and 30d windows.

#### `theme_memory_consolidation`

Save durable repeated patterns and narratives.

#### `candidate_research`

Run deeper research on top-ranked candidates.

#### `candidate_alerts`

Send Telegram brief when score crosses `SIGNAL_ALERT_THRESHOLD`.

### Scheduling Policy

Suggested cadence:

- `news_refresh`: existing interval
- `market_snapshot_refresh`: every 5 to 15 minutes during market hours, configurable
- `mention_extraction`: after news refresh or every scheduler tick with unprocessed limit
- `popularity_scoring`: every 15 minutes
- `theme_memory_consolidation`: hourly or daily
- `candidate_research`: when top scores change materially
- `candidate_alerts`: when threshold crossing occurs and cooldown permits

### Job Isolation

Each job should:

- Start a job run or runtime run.
- Emit step-level metadata.
- Commit after successful units of work.
- Record partial failure without hiding successful work.
- Respect retention cleanup.

## 14. Router And Command Integration

### Router Changes

Add intents:

- `research`
- `candidates`
- `signals`
- `researchstatus`

Add command mapping:

- `/research`
- `/candidates`
- `/signals`
- `/researchstatus`

The router should map these to a research capability, but the planner should decide the detailed work.

### Handler Changes

Telegram handlers should:

1. Parse command and args.
2. Build planner input from message, user context, and command.
3. Invoke `PlannerAgent`.
4. Run the selected research workflow.
5. Format response through `ReportAgent`.

## 15. Configuration

Settings should stay explicit and env-driven.

Add:

```text
MARKET_UNIVERSE_DEFAULT=sp500,nasdaq100,watchlist
SIGNAL_RETENTION_DAYS=30
SIGNAL_ALERT_THRESHOLD=75
SIGNAL_ALERT_COOLDOWN_MINUTES=360
SIGNAL_WEIGHT_MENTION_VELOCITY=1.0
SIGNAL_WEIGHT_SOURCE_DIVERSITY=1.0
SIGNAL_WEIGHT_RECENCY=1.0
SIGNAL_WEIGHT_SEMANTIC_SIMILARITY=1.0
SIGNAL_WEIGHT_PRICE_MOMENTUM=1.0
SIGNAL_WEIGHT_VOLUME=1.0
SIGNAL_WEIGHT_THEME_PERSISTENCE=1.0
SIGNAL_WEIGHT_TRUST=1.0
SOCIAL_SIGNALS_ENABLED=false
LLM_MENTION_EXTRACTION_ENABLED=false
```

Also change default:

```text
SNAPSHOT_RETENTION_DAYS=30
```

## 16. Safety And Guardrails

Every stock-related answer must preserve financial guardrails:

- No personalized buy/sell instructions.
- No guaranteed future performance claims.
- Use "attention and momentum research ranking" language.
- Show evidence and weak evidence.
- Mention that rankings can be wrong, stale, or driven by noisy source concentration.

Sensitive data rules:

- Treat Telegram data, transcripts, learned memories, source credentials, and user watchlists as sensitive.
- Do not expose private user memory in shared or global brief contexts.
- Keep social provider credentials config-driven.

## 17. Observability

Use existing runtime observability for:

- Planner decisions.
- Agents selected.
- Retrieval counts.
- Extraction counts.
- Score windows.
- Top candidate changes.
- Alert threshold crossings.
- Provider failures.
- Missing data cases.

Each research workflow should record:

- `workflow`
- `trigger`
- `plan`
- `step_name`
- `step_type`
- `status`
- `duration_ms`
- `metadata`
- `error_message`

This makes `/researchstatus`, `/job`, `/trace`, and `/step` useful for research workflows.

## 18. Implementation Phases

### Phase 1: Data Model And Settings

Deliverables:

- Add settings for retention, thresholds, universe, and score weights.
- Change snapshot retention default to 30.
- Add SQLAlchemy models for market entities, mentions, signal snapshots, and theme memories.
- Add Alembic migration.
- Add repository methods for creating, reading, aggregating, and pruning signal data.

Tests:

- Migration imports and schema creation.
- Repository CRUD for mentions, snapshots, and theme memories.
- Retention cleanup behavior.

### Phase 2: Planner And Schemas

Deliverables:

- Add research schemas.
- Add deterministic planner behavior for commands.
- Optionally add LLM planner hook behind config.
- Add router intents and command mapping.

Tests:

- `/research` produces deep research plan.
- `/candidates` produces ranking plan.
- `/signals MU` produces ticker-specific signal plan.
- General market questions route to planner when appropriate.

### Phase 3: Extraction And Scoring

Deliverables:

- Add deterministic mention extraction.
- Add theme extraction with keyword maps.
- Add score calculator with configurable weights.
- Store component scores.
- Save signal snapshots by window.

Tests:

- Ticker extraction from article title, body, summary, and related tickers.
- Theme extraction from configured keywords.
- Weighted totals sort candidates correctly.
- Missing price or volume data does not fail scoring.

### Phase 4: Retrieval And Analysis

Deliverables:

- Add `retrieve_market_context(plan)`.
- Use 30-day date filters.
- Add lexical filters for ticker/theme/company aliases.
- Integrate article, summary, mention, market snapshot, signal snapshot, and theme memory data.
- Add candidate explanation builder.

Tests:

- 30-day article and summary context is used.
- Ticker filters narrow results.
- Theme filters retrieve related candidates.
- Explanations include evidence and weak-confidence flags.

### Phase 5: Scheduler Jobs

Deliverables:

- Add research scheduler job functions.
- Integrate them into scheduler graph or scheduler service.
- Add runtime traces for each job.
- Add threshold-based candidate alerts with cooldown.

Tests:

- Each job runs independently.
- Scoring job writes snapshots.
- Alert job sends only when threshold crossed.
- Runtime traces and errors are recorded.

### Phase 6: Telegram UX

Deliverables:

- Add `/research`, `/candidates`, `/signals`, `/researchstatus`.
- Add report formatting.
- Add financial guardrail text.
- Ensure Telegram output is concise and readable.

Tests:

- Command handler responses.
- Candidate output includes scores and evidence.
- Signal output includes weak evidence.
- Research status output uses runtime data.

### Phase 7: Optional Social And Filings Enhancements

Deliverables:

- Add source-config-driven social signal ingestion.
- Add filings or IR feed provider.
- Add LLM extraction behind config.
- Add PDF report output later.

Tests:

- Disabled optional providers do nothing.
- Enabled providers store source family correctly.
- LLM extraction validates schema and falls back cleanly.

## 19. Suggested File Changes

### New Files

```text
src/news_agent/research/__init__.py
src/news_agent/research/schemas.py
src/news_agent/research/planner.py
src/news_agent/research/extraction.py
src/news_agent/research/scoring.py
src/news_agent/research/retrieval.py
src/news_agent/research/analysis.py
src/news_agent/research/reporting.py
src/news_agent/research/agents.py
src/news_agent/research/scheduler.py
tests/test_research_planner.py
tests/test_research_extraction.py
tests/test_research_scoring.py
tests/test_research_retrieval.py
tests/test_research_scheduler.py
tests/test_market_signal_repositories.py
migrations/versions/0005_market_research_signals.py
```

### Existing Files To Update

```text
src/news_agent/settings.py
src/news_agent/storage/models.py
src/news_agent/storage/repositories.py
src/news_agent/storage/retrieval.py
src/news_agent/agent/router.py
src/news_agent/bot/handlers.py
src/news_agent/graph/scheduler_graph.py
src/news_agent/scheduler/service.py
src/news_agent/scheduler/jobs.py
.env.example
README.md
```

## 20. Test Plan

Run focused tests during implementation:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_research_planner.py
PYTHONPATH=src .venv/bin/pytest tests/test_research_scoring.py
PYTHONPATH=src .venv/bin/pytest tests/test_market_signal_repositories.py
PYTHONPATH=src .venv/bin/pytest tests/test_research_retrieval.py
PYTHONPATH=src .venv/bin/pytest tests/test_research_scheduler.py
```

Run existing regression suite before completion:

```bash
PYTHONPATH=src .venv/bin/pytest
```

Also verify:

- Alembic migration upgrades cleanly.
- Scheduler can run one research tick.
- Telegram command handlers produce guarded responses.
- Existing `/brief`, `/stocks`, `/runtime`, and source admin commands still work.

## 21. Release Criteria

The first release is complete when:

1. Market signal tables are migrated and repositories are tested.
2. Planner produces structured research plans for new commands.
3. Mention extraction and scoring produce ranked candidates.
4. Retrieval uses 30-day market/news context.
5. Candidate explanations include evidence, score components, and weak-signal caveats.
6. Scheduled jobs can refresh, extract, score, and alert.
7. Telegram commands work end to end.
8. Existing tests pass.
9. Financial guardrails are present in every stock-related report.

## 22. Open Questions

1. Should the first universe be a static checked-in symbol list or fetched from a provider?
2. Should market signal snapshots be global, per user, or both?
3. Should watchlist names receive a score boost for that user only?
4. What alert cooldown is acceptable for high-volatility news days?
5. Which source families should count as high-trust by default?
6. Should theme memories be global or user-specific in the first release?
7. Should social signals be excluded until explicit provider credentials are configured?

## 23. Recommended First Implementation Slice

Start with a narrow vertical slice:

1. Add models, migration, settings, and repositories.
2. Implement deterministic planner for `/candidates` and `/signals <ticker>`.
3. Extract mentions from article titles and summaries.
4. Score candidates using mention velocity, source diversity, recency, trust, and existing market snapshot percent change.
5. Add candidate and signal report formatting.
6. Add tests for planner, repository, extraction, scoring, and output guardrails.

This slice proves the product loop without requiring social providers, filings APIs, or full LLM extraction.
