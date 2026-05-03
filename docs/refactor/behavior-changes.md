# Behavior Changes

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

