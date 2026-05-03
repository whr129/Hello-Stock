# State Contract

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

