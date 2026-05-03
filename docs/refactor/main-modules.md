# Main Modules

## Orchestration
- `src/news_agent/app/supervisor.py`
  - top-level LangGraph builder
  - supervisor node implementations
  - final response merge, guardrails, reflection, and session persistence
- `src/news_agent/app/state.py`
  - shared supervisor state contracts
- `src/news_agent/agent/reflection.py`
  - LLM reflection prompt, decision parsing, and retry/fail verdict handling

## News Domain
- `src/news_agent/domains/news/subagent.py`
  - source management
  - preference updates
  - memory administration
  - retrieval-backed news brief generation

## Market Domain
- `src/news_agent/domains/market/subagent.py`
  - watchlist administration
  - live market data fetch
  - stored snapshot fallback
  - technical-analysis response composition

## Runtime Domain
- `src/news_agent/domains/runtime/subagent.py`
  - runtime history lookup
  - job and step inspection
  - recent failure summaries
  - operator-facing debugging responses

## Observability
- `src/news_agent/observability/`
  - runtime event recording
  - trace persistence
  - alert dispatch
- `src/news_agent/scheduler/service.py`
  - manual refresh summaries
  - recap delivery
  - memory consolidation job processing
  - runtime alert trigger points for scheduler failures

## Compatibility
- `src/news_agent/graph/chat_graph.py`
  - stable entrypoint now delegating to the supervisor graph
- `src/news_agent/agent/router.py`
  - parsing plus capability-oriented route decisions

