# Memory Consolidation

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

