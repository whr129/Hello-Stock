# Runtime Flow

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
- natural-language operational questions like "what happened in the last refresh?" or "why did twitter fetch fail?" -> `runtime_agent`
- unsupported general chat -> constrained help response or web search fallback, depending on route policy

