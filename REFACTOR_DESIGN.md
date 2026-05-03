# News Agent Refactor Design

## Overview
The application is refactored around a LangGraph supervisor that routes each incoming request to one or more specialized subagents:

- `news_agent`: personalized news briefs, source management, topics, local preferences, recap settings, and memory operations
- `market_agent`: watchlist management, live quote lookup, stored snapshot fallback, and technical analysis
- `runtime_agent`: runtime inspection, alerting, trace lookup, refresh debugging, and execution-history analysis

The Telegram bot, scheduler, and persistence model remain in place. The active chat path no longer uses the previous generic tool registry or free-form ReAct fallback.

## 2. Memory consolidation
Memory remains split into two layers:

- short-term memory: LangGraph-managed message state scoped to a Telegram chat thread
- long-term memory: vector-backed durable user memory consolidated asynchronously

Short-term memory should be maintained as rolling thread state and persisted between requests. It is used for immediate conversational continuity and `/memory` inspection.

Long-term memory should no longer be stored synchronously or as simple append-only records on every qualifying message. New durable memory candidates should be consolidated into the existing memory pool by merging related information, resolving conflicts, and minimizing redundant records.

The scheduler-driven batch flow is:

1. persist a conversation transcript
2. enqueue a memory consolidation job after every 20 new user messages
3. run extraction asynchronously in the scheduler loop
4. extract durable, atomic memory candidates
5. consolidate each candidate asynchronously against existing vector memories

For each newly extracted memory candidate, consolidation should retrieve the top semantically similar active memories from the long-term store. Retrieval must be restricted to the same namespace and strategy. In this repository, namespace maps to `user_id`, and strategy maps to `memory_type`.

The consolidation step should send the new candidate plus the retrieved existing memories to the LLM. The LLM should preserve semantic context when comparing memories so equivalent statements, such as "loves pizza" and "likes pizza", do not trigger unnecessary updates. It should return exactly one canonical outcome for each candidate:

- `add`: create a new durable memory when no related active memory already captures the information
- `update`: merge the candidate into an existing memory when it adds useful detail or resolves a conflict
- `skip`: ignore the candidate when it is redundant, low value, or already semantically represented

The long-term memory store should therefore support semantic retrieval, deduplication, conflict-aware updates, and lifecycle-aware active-memory filtering rather than only latest-first listing.

## Runtime Flow
The interactive graph now runs the following sequence:

1. `load_user_context`
2. `classify_request`
3. `route_request`
4. `run_news_agent`, `run_market_agent`, and/or `run_runtime_agent`
5. `merge_agent_outputs`
6. `guardrail_check`
7. `reflect_result`
8. `persist_session`

`reflect_result` is an LLM-backed quality-control node. It evaluates the original user request, classified intent, route, completed agents, subagent/search metadata, and guarded final response. It returns `pass`, `retry`, or `fail`.

- `pass` continues to persistence.
- `retry` may update intent and args, clear previous subagent/search outputs, and route again.
- `fail` or exhausted retries returns the best available answer with a short user-facing note.

Reflection retries are bounded by `ANSWER_REFLECTION_MAX_RETRIES`, defaulting to one. Reflection can be disabled with `ANSWER_REFLECTION_ENABLED=false`.

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
  - final response merge, guardrails, reflection, and session persistence
- `src/news_agent/app/state.py`
  - shared supervisor state contracts
- `src/news_agent/agent/reflection.py`
  - LLM reflection prompt, decision parsing, and retry/fail verdict handling

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
  - memory consolidation job processing
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
- `messages`: LangGraph-compatible short-term message state for the active chat thread
- route metadata: ordered agents, requested capabilities, fallback response
- subagent outputs: `news_result`, `market_result`, `runtime_result`
- reflection metadata: attempts, latest decision, notes, and exhaustion flag
- final output: `final_response`

The memory subsystem additionally owns:

- `ConversationEvent`: persisted user/assistant transcript rows
- `MemoryConsolidationJob`: pending/running/completed async memory jobs
- upgraded `LongTermMemory`: vector-backed durable memory items with lifecycle metadata

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
- Candidate answers are reflected before persistence; clearly wrong routes can be retried once by default.
- Short-term memory is maintained with LangGraph-style message state instead of a custom plain dict list.
- Long-term memory is extracted asynchronously in batches of 20 user messages and consolidated into a vector-backed memory pool.
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

Equivalent natural-language queries should also route to `runtime_agent` when they are clearly about execution history, refresh behavior, or recent runtime errors. Trace output includes run metadata, step metadata, parent/child nesting when recorded, and any runtime errors for the run.

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
