# News Agent Refactor Design

## Overview
The application is refactored around a LangGraph supervisor that routes each incoming request to one or more specialized subagents:

- `news_agent`: personalized news briefs, source management, topics, local preferences, recap settings, and memory operations
- `market_agent`: watchlist management, live quote lookup, stored snapshot fallback, and technical analysis
- `runtime_agent`: runtime inspection, alerting, trace lookup, refresh debugging, and execution-history analysis

The Telegram bot, scheduler, and persistence model remain in place. The active chat path no longer uses the previous generic tool registry or free-form ReAct fallback.

## Runtime Flow
The interactive graph now runs the following sequence:

1. `load_user_context`
2. `classify_request`
3. `route_request`
4. `run_news_agent`, `run_market_agent`, and/or `run_runtime_agent`
5. `merge_agent_outputs`
6. `guardrail_check`
7. `persist_session`

Routing is capability-driven:

- `/brief` -> `news_agent`
- `/stocks`, `/watch`, `/unwatch` -> `market_agent`
- `/topics`, `/local`, `/sources`, `/addsource`, `/removesource`, `/memory`, `/forget`, `/resetmemory` -> `news_agent`
- `/runtime`, `/job`, `/trace`, `/step`, `/alerts` -> `runtime_agent`
- mixed requests with both news and stock intent -> `news_agent` plus `market_agent` in deterministic order
- natural-language operational questions like “what happened in the last refresh?” or “why did twitter fetch fail?” -> `runtime_agent`
- unsupported general chat -> constrained help response or web search fallback, depending on route policy

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

### Runtime domain
- `src/news_agent/domains/runtime/subagent.py`
  - runtime history lookup
  - job and step inspection
  - recent failure summaries
  - operator-facing debugging responses

### Observability
- `src/news_agent/observability/`
  - runtime event recording
  - trace persistence
  - alert dispatch
- `src/news_agent/scheduler/service.py`
  - manual refresh summaries
  - recap delivery
  - runtime alert trigger points for scheduler failures

### Compatibility
- `src/news_agent/graph/chat_graph.py`
  - stable entrypoint now delegating to the supervisor graph
- `src/news_agent/agent/router.py`
  - parsing plus capability-oriented route decisions

## State Contract
The supervisor state owns:

- request fields: user/chat/message, command, args, intent
- parsed entities: requested symbols, requested topics
- `user_context`: watchlist, topics, local region, timezone, short-term memory, long-term memory
- route metadata: ordered agents, requested capabilities, fallback response
- subagent outputs: `news_result`, `market_result`, `runtime_result`
- final output: `final_response`

The runtime subsystem additionally owns persistent operational state:

- `RunRecord`: one row per chat request, scheduler refresh, recap run, or manual operation
- `StepTrace`: ordered step history for supervisor nodes, subagent calls, tool/provider calls, and scheduler steps
- `ErrorRecord`: normalized runtime failures with step name, run id, workflow, and concise error details
- alert-delivery metadata for operator notifications

Each trace step should record:

- `run_id`
- `workflow` such as `chat`, `scheduler`, `manual_refresh`, or `daily_recap`
- `step_name`
- `step_type` such as `node`, `subagent`, `provider`, or `tool`
- `parent_step_id`
- `started_at`, `completed_at`, `duration_ms`
- `status`: `running`, `completed`, `failed`, or `skipped`
- compact structured metadata
- optional `error_message`

## Behavior Changes
- The old `ToolRegistry` no longer powers the active chat path.
- Free-form `general_chat` handling is replaced by constrained help or explicit web-search routing.
- Financial guardrails are applied only when market output is included.
- Mixed requests can now combine a news brief with market analysis in one turn.
- Every chat request and scheduler refresh should emit runtime traces that preserve the sequence of node, subagent, and provider calls.
- Failed or completed-with-errors runs should trigger a Telegram admin alert to a configured operator chat.
- Runtime history is queryable so an operator can inspect a refresh step, a specific call chain, or recent failures for debugging.

## Runtime Query Surface
`runtime_agent` should expose explicit operational commands:

- `/runtime` for the latest run summary
- `/job <run-id>` for a specific run
- `/trace <run-id>` for the ordered step sequence
- `/step <run-id> <step-name>` for one refresh step or node/provider call
- `/alerts` for recent failures and alertable events

Equivalent natural-language queries should also route to `runtime_agent` when they are clearly about execution history, refresh behavior, or recent runtime errors.

## Alerting
Runtime alerts should be delivered to a configured Telegram admin chat, not to every end user. Alerts should include:

- workflow type
- run id
- failing step
- concise error summary
- related source, ticker, or user when relevant

User-facing command failures still return normal responses in-chat; operator alerts are separate.

## Validation
Verified with:

```bash
PYTHONPATH=src .venv/bin/pytest
```

Current result: full suite passing.
