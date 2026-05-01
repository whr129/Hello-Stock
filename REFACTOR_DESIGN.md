# News Agent Refactor Design

## Overview
The application is refactored around a LangGraph supervisor that routes each incoming request to one or both domain subagents:

- `news_agent`: personalized news briefs, source management, topics, local preferences, and memory operations
- `market_agent`: watchlist management, live quote lookup, stored snapshot fallback, and technical analysis

The Telegram bot, scheduler, and persistence model remain in place. The active chat path no longer uses the previous generic tool registry or free-form ReAct fallback.

## Runtime Flow
The interactive graph now runs the following sequence:

1. `load_user_context`
2. `classify_request`
3. `route_request`
4. `run_news_agent` and/or `run_market_agent`
5. `merge_agent_outputs`
6. `guardrail_check`
7. `persist_session`

Routing is capability-driven:

- `/brief` -> `news_agent`
- `/stocks`, `/watch`, `/unwatch` -> `market_agent`
- `/topics`, `/local`, `/sources`, `/addsource`, `/removesource`, `/memory`, `/forget`, `/resetmemory` -> `news_agent`
- mixed requests with both news and stock intent -> both subagents in deterministic order
- unsupported general chat -> constrained help response

## Main Modules
### Orchestration
- `src/news_agent/app/supervisor.py`
  - top-level LangGraph builder
  - supervisor node implementations
  - final response merge, guardrails, and session persistence
- `src/news_agent/app/state.py`
  - shared supervisor state contracts

### News domain
- `src/news_agent/domains/news/subagent.py`
  - source management
  - preference updates
  - memory administration
  - retrieval-backed news brief generation

### Market domain
- `src/news_agent/domains/market/subagent.py`
  - watchlist administration
  - live market data fetch
  - stored snapshot fallback
  - technical-analysis response composition

### Compatibility
- `src/news_agent/graph/chat_graph.py`
  - stable entrypoint now delegating to the supervisor graph
- `src/news_agent/agent/router.py`
  - parsing plus capability-oriented route decisions

## State Contract
The supervisor state owns:

- request fields: user/chat/message, command, args, intent
- parsed entities: requested symbols, requested topics
- `user_context`: watchlist, topics, local region, short-term memory, long-term memory
- route metadata: ordered agents, requested capabilities, fallback response
- subagent outputs: `news_result`, `market_result`
- final output: `final_response`

## Behavior Changes
- The old `ToolRegistry` no longer powers the active chat path.
- Free-form `general_chat` handling is replaced by a constrained help response.
- Financial guardrails are applied only when market output is included.
- Mixed requests can now combine a news brief with market analysis in one turn.

## Validation
Verified with:

```bash
PYTHONPATH=src .venv/bin/pytest
```

Current result: full suite passing.
