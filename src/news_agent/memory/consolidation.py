from __future__ import annotations

import json
from dataclasses import dataclass

from openai import APIError, AsyncOpenAI
from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.memory.embeddings import EmbeddingService
from news_agent.observability.runtime import RuntimeAlertService, RuntimeTraceService
from news_agent.settings import Settings
from news_agent.storage.database import session_scope
from news_agent.storage.models import ConversationEvent, LongTermMemory, MemoryType, User
from news_agent.storage.repositories import (
    ConversationEventRepository,
    EmbeddingRepository,
    MemoryConsolidationJobRepository,
    MemoryRepository,
    UserRepository,
)

EXTRACTION_PROMPT = """
You extract durable user memories from a Telegram conversation transcript.

Return JSON with this shape:
{"candidates":[
  {
    "text":"...",
    "category":"preference|profile|location|watch_habit|constraint|other",
    "confidence":0.0
  }
]}

Rules:
- Extract only durable, reusable user facts or preferences.
- Ignore one-off requests, greetings, and temporary context.
- Prefer atomic memories.
- Return at most the requested candidate limit.
""".strip()

TURN_EXTRACTION_PROMPT = """
You extract durable user memories from the latest Telegram chat turn.

Return JSON with this shape:
{"candidates":[
  {
    "text":"...",
    "category":"preference|profile|location|watch_habit|constraint|other",
    "confidence":0.0
  }
]}

Rules:
- Extract only facts or preferences that should be reused in future conversations.
- Include profile facts such as the user's preferred name.
- Include location facts and local-news preferences.
- Include durable communication preferences and constraints.
- Ignore questions, greetings, one-off tasks, and assistant claims.
- Prefer normalized third-person wording, for example "User's preferred name is Howard."
- Return an empty candidate list when there is nothing durable to remember.
""".strip()

CONSOLIDATION_PROMPT = """
You decide how to merge a candidate memory into an existing memory pool.

Return JSON with this shape:
{"action":"add|update|skip","memory_id":123|null,"text":"...", "category":"...", "confidence":0.0}

Rules:
- Use `add` when the candidate is new and useful.
- Use `update` when one existing memory should be revised or clarified.
- Use `skip` when the candidate is transient, duplicated, or too weak.
- Only choose `update` if an existing memory is clearly the same fact or preference.
- Treat semantically equivalent wording as already represented. For example, "likes pizza" and
  "loves pizza" should usually be skipped unless the candidate adds meaningful new detail.
- Resolve conflicts by updating the most relevant existing memory with the best current durable
  fact.
""".strip()


@dataclass(frozen=True)
class MemoryCandidate:
    text: str
    category: str
    confidence: float


@dataclass(frozen=True)
class MemoryDecision:
    action: str
    memory_id: int | None
    text: str
    category: str
    confidence: float


class MemoryConsolidationService:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.embedding_service = EmbeddingService(settings)
        self.trace_service = RuntimeTraceService(session_factory, settings)
        self.alert_service = RuntimeAlertService(session_factory, settings)
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key)
            if settings.openai_api_key
            else None
        )

    async def enqueue_if_due(self, *, user_id: int) -> bool:
        async with session_scope(self.session_factory) as session:
            user = await session.get(User, user_id)
            if user is None:
                return False
            job_repo = MemoryConsolidationJobRepository(session)
            if await job_repo.has_active_job(user_id):
                return False
            event_repo = ConversationEventRepository(session)
            events = await event_repo.list_oldest_unprocessed_user_events(
                user_id=user_id,
                after_event_id=user.memory_cursor_event_id,
                limit=self.settings.long_term_memory_batch_size,
            )
            if len(events) < self.settings.long_term_memory_batch_size:
                return False
            await job_repo.create(
                user_id=user.id,
                source_start_event_id=events[0].id,
                source_end_event_id=events[-1].id,
                message_count=len(events),
            )
            return True

    async def process_due_jobs(self, *, limit: int = 2) -> int:
        processed = 0
        async with session_scope(self.session_factory) as session:
            pending_jobs = await MemoryConsolidationJobRepository(session).list_pending(limit=limit)
            job_ids = [job.id for job in pending_jobs]

        for job_id in job_ids:
            await self._process_job(job_id)
            processed += 1
        return processed

    async def reset_user_state(self, *, user_id: int) -> None:
        async with session_scope(self.session_factory) as session:
            memory_repo = MemoryRepository(session)
            job_repo = MemoryConsolidationJobRepository(session)
            event_repo = ConversationEventRepository(session)
            await memory_repo.reset_learned(user_id)
            await job_repo.delete_for_user(user_id)
            latest_event_id = await event_repo.latest_event_id_for_user(user_id)
            await UserRepository(session, self.settings).update_memory_cursor(
                user_id,
                latest_event_id,
            )

    async def _process_job(self, job_id: int) -> None:
        async with session_scope(self.session_factory) as session:
            job_repo = MemoryConsolidationJobRepository(session)
            job = await job_repo.mark_running(job_id)
            if job is None:
                return
            user_id = job.user_id

        run_id = await self.trace_service.ensure_run(
            workflow="memory_consolidation",
            trigger="scheduler",
            metadata={"job_id": job_id, "user_id": user_id},
        )
        root_step_id = await self.trace_service.start_step(
            run_id=run_id,
            workflow="memory_consolidation",
            step_name=f"memory_job:{job_id}",
            step_type="job",
            metadata={"user_id": user_id},
        )
        try:
            async with session_scope(self.session_factory) as session:
                job_repo = MemoryConsolidationJobRepository(session)
                event_repo = ConversationEventRepository(session)
                memory_repo = MemoryRepository(session)
                embedding_repo = EmbeddingRepository(session)
                user_repo = UserRepository(session, self.settings)
                job = await job_repo.get(job_id)
                if job is None:
                    return
                events = await event_repo.list_between_ids(
                    user_id=job.user_id,
                    start_event_id=job.source_start_event_id,
                    end_event_id=job.source_end_event_id,
                )
                extraction_step = await self.trace_service.start_step(
                    run_id=run_id,
                    workflow="memory_consolidation",
                    step_name="extract_candidates",
                    step_type="tool",
                    parent_step_id=root_step_id,
                    metadata={"event_count": len(events)},
                )
                candidates = await self.extract_candidates(events)
                await self.trace_service.finish_step(
                    extraction_step,
                    status="completed",
                    metadata={"candidate_count": len(candidates)},
                )

                update_count = 0
                add_count = 0
                skip_count = 0
                for candidate in candidates[: self.settings.memory_candidates_per_batch]:
                    decision_step = await self.trace_service.start_step(
                        run_id=run_id,
                        workflow="memory_consolidation",
                        step_name="consolidate_candidate",
                        step_type="tool",
                        parent_step_id=root_step_id,
                        metadata={"candidate": candidate.text[:200]},
                    )
                    candidate_embedding = await self.embedding_service.embed_text(candidate.text)
                    nearest = await memory_repo.nearest_for_user(
                        user_id=job.user_id,
                        memory_type=MemoryType.LEARNED,
                        query_embedding=candidate_embedding,
                        limit=self.settings.long_term_memory_top_k,
                    )
                    decision = await self.consolidate_candidate(candidate, nearest)
                    if decision.action == "add":
                        memory_embedding = await self._embedding_for_decision(
                            candidate=candidate,
                            candidate_embedding=candidate_embedding,
                            decision=decision,
                        )
                        memory = await memory_repo.remember(
                            user_id=job.user_id,
                            text=decision.text,
                            memory_type=MemoryType.LEARNED,
                            source="memory_job",
                            confidence=decision.confidence,
                            category=decision.category,
                            source_job_id=job.id,
                        )
                        await embedding_repo.replace_memory_embedding(
                            memory.id,
                            memory_embedding,
                            self.settings.embedding_model,
                        )
                        add_count += 1
                    elif decision.action == "update" and decision.memory_id:
                        memory_embedding = await self._embedding_for_decision(
                            candidate=candidate,
                            candidate_embedding=candidate_embedding,
                            decision=decision,
                        )
                        memory = await memory_repo.update_memory(
                            memory_id=decision.memory_id,
                            text=decision.text,
                            category=decision.category,
                            confidence=decision.confidence,
                            source_job_id=job.id,
                        )
                        if memory:
                            await embedding_repo.replace_memory_embedding(
                                memory.id,
                                memory_embedding,
                                self.settings.embedding_model,
                            )
                            update_count += 1
                        else:
                            skip_count += 1
                    else:
                        if decision.memory_id:
                            await memory_repo.mark_seen(decision.memory_id)
                        skip_count += 1

                    await self.trace_service.finish_step(
                        decision_step,
                        status="completed",
                        metadata={"action": decision.action, "memory_id": decision.memory_id},
                    )

                await user_repo.update_memory_cursor(job.user_id, job.source_end_event_id)
                await job_repo.mark_completed(job.id)
                await self.trace_service.finish_step(
                    root_step_id,
                    status="completed",
                    metadata={"added": add_count, "updated": update_count, "skipped": skip_count},
                )
                await self.trace_service.finish_run(
                    run_id,
                    status="completed",
                    summary=(
                        f"memory_consolidation completed with {add_count} add(s), "
                        f"{update_count} update(s), {skip_count} skip(s)"
                    ),
                )

                next_events = await event_repo.list_oldest_unprocessed_user_events(
                    user_id=job.user_id,
                    after_event_id=job.source_end_event_id,
                    limit=self.settings.long_term_memory_batch_size,
                )
                has_next_batch = len(next_events) >= self.settings.long_term_memory_batch_size
                active_job_exists = await job_repo.has_active_job(job.user_id)
                if has_next_batch and not active_job_exists:
                    await job_repo.create(
                        user_id=job.user_id,
                        source_start_event_id=next_events[0].id,
                        source_end_event_id=next_events[-1].id,
                        message_count=len(next_events),
                    )
        except Exception as exc:
            await self.trace_service.finish_step(
                root_step_id,
                status="failed",
                error_message=str(exc),
            )
            error_id = await self.trace_service.record_error(
                run_id=run_id,
                workflow="memory_consolidation",
                step_name=f"memory_job:{job_id}",
                error_message=str(exc),
                step_id=root_step_id,
                metadata={"job_id": job_id},
            )
            await self.trace_service.finish_run(run_id, status="failed", summary=str(exc)[:500])
            await self.alert_service.send_alert(
                run_id=run_id,
                error_id=error_id,
                message_text=(
                    "Runtime alert\n"
                    "- Workflow: memory_consolidation\n"
                    f"- Run: {run_id}\n"
                    f"- Step: memory_job:{job_id}\n"
                    f"- Error: {exc}"
                ),
            )
            async with session_scope(self.session_factory) as session:
                await MemoryConsolidationJobRepository(session).mark_failed(
                    job_id,
                    error_message=str(exc),
                    max_retries=self.settings.memory_job_max_retries,
                )

    async def extract_candidates(self, events: list[ConversationEvent]) -> list[MemoryCandidate]:
        transcript = "\n".join(f"{event.role}: {event.content}" for event in events)
        if not transcript.strip():
            return []

        if self.client is not None:
            try:
                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": EXTRACTION_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Candidate limit: {self.settings.memory_candidates_per_batch}\n"
                                f"Transcript:\n{transcript[:8000]}"
                            ),
                        },
                    ],
                    temperature=0.1,
                )
                content = response.choices[0].message.content or "{}"
                payload = json.loads(content)
                return _memory_candidates_from_payload(
                    payload,
                    limit=self.settings.memory_candidates_per_batch,
                )
            except (APIError, ValueError, TypeError, json.JSONDecodeError):
                pass

        return []

    async def extract_turn_candidates(
        self,
        *,
        user_message: str,
        assistant_response: str = "",
    ) -> list[MemoryCandidate]:
        if not user_message.strip() or self.client is None:
            return []

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": TURN_EXTRACTION_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Candidate limit: {self.settings.memory_candidates_per_batch}\n"
                            "Latest turn:\n"
                            f"user: {user_message[:4000]}\n"
                            f"assistant: {assistant_response[:4000]}"
                        ),
                    },
                ],
                temperature=0,
            )
            content = response.choices[0].message.content or "{}"
            payload = json.loads(content)
            return _memory_candidates_from_payload(
                payload,
                limit=self.settings.memory_candidates_per_batch,
            )
        except (APIError, ValueError, TypeError, json.JSONDecodeError):
            return []

    async def remember_turn(
        self,
        *,
        user_id: int,
        user_message: str,
        assistant_response: str = "",
    ) -> dict[str, int]:
        candidates = await self.extract_turn_candidates(
            user_message=user_message,
            assistant_response=assistant_response,
        )
        if not candidates:
            return {"added": 0, "updated": 0, "skipped": 0}

        added = 0
        updated = 0
        skipped = 0
        async with session_scope(self.session_factory) as session:
            memory_repo = MemoryRepository(session)
            embedding_repo = EmbeddingRepository(session)
            for candidate in candidates:
                candidate_embedding = await self.embedding_service.embed_text(candidate.text)
                nearest = await memory_repo.nearest_for_user(
                    user_id=user_id,
                    memory_type=MemoryType.EXPLICIT,
                    query_embedding=candidate_embedding,
                    limit=self.settings.long_term_memory_top_k,
                )
                decision = await self.consolidate_candidate(candidate, nearest)
                if decision.action == "add":
                    memory_embedding = await self._embedding_for_decision(
                        candidate=candidate,
                        candidate_embedding=candidate_embedding,
                        decision=decision,
                    )
                    memory = await memory_repo.remember(
                        user_id=user_id,
                        text=decision.text,
                        memory_type=MemoryType.EXPLICIT,
                        source="chat_turn",
                        confidence=decision.confidence,
                        category=decision.category,
                    )
                    await embedding_repo.replace_memory_embedding(
                        memory.id,
                        memory_embedding,
                        self.settings.embedding_model,
                    )
                    added += 1
                elif decision.action == "update" and decision.memory_id:
                    memory_embedding = await self._embedding_for_decision(
                        candidate=candidate,
                        candidate_embedding=candidate_embedding,
                        decision=decision,
                    )
                    memory = await memory_repo.update_memory(
                        memory_id=decision.memory_id,
                        text=decision.text,
                        category=decision.category,
                        confidence=decision.confidence,
                    )
                    if memory:
                        await embedding_repo.replace_memory_embedding(
                            memory.id,
                            memory_embedding,
                            self.settings.embedding_model,
                        )
                        updated += 1
                    else:
                        skipped += 1
                else:
                    if decision.memory_id:
                        await memory_repo.mark_seen(decision.memory_id)
                    skipped += 1
        return {"added": added, "updated": updated, "skipped": skipped}

    async def consolidate_candidate(
        self,
        candidate: MemoryCandidate,
        nearest: list[tuple[LongTermMemory, float]],
    ) -> MemoryDecision:
        if self.client is not None:
            try:
                existing_text = "\n".join(
                    (
                        f"- id={memory.id} distance={distance:.4f} "
                        f"category={memory.category} text={memory.memory_text}"
                    )
                    for memory, distance in nearest
                ) or "- none"
                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": CONSOLIDATION_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Candidate:\ntext={candidate.text}\n"
                                f"category={candidate.category}\nconfidence={candidate.confidence}\n\n"
                                f"Existing memories:\n{existing_text}"
                            ),
                        },
                    ],
                    temperature=0.1,
                )
                content = response.choices[0].message.content or "{}"
                payload = json.loads(content)
                action = str(payload.get("action", "skip")).strip().lower()
                if action not in {"add", "update", "skip"}:
                    action = "skip"
                memory_id = payload.get("memory_id")
                if not isinstance(memory_id, int):
                    memory_id = None
                return MemoryDecision(
                    action=action,
                    memory_id=memory_id,
                    text=str(payload.get("text", candidate.text)).strip(),
                    category=str(payload.get("category", candidate.category)).strip() or "general",
                    confidence=float(payload.get("confidence", candidate.confidence)),
                )
            except (APIError, ValueError, TypeError, json.JSONDecodeError):
                pass

        if nearest:
            memory, distance = nearest[0]
            if distance < 0.15:
                return MemoryDecision(
                    action="update",
                    memory_id=memory.id,
                    text=candidate.text,
                    category=candidate.category,
                    confidence=max(candidate.confidence, memory.confidence),
                )
            if distance < 0.25:
                return MemoryDecision(
                    action="skip",
                    memory_id=memory.id,
                    text=memory.memory_text,
                    category=memory.category,
                    confidence=memory.confidence,
                )
        return MemoryDecision(
            action="add",
            memory_id=None,
            text=candidate.text,
            category=candidate.category,
            confidence=candidate.confidence,
        )

    async def _embedding_for_decision(
        self,
        *,
        candidate: MemoryCandidate,
        candidate_embedding: list[float],
        decision: MemoryDecision,
    ) -> list[float]:
        if decision.text.strip() == candidate.text.strip():
            return candidate_embedding
        return await self.embedding_service.embed_text(decision.text)


def _memory_candidates_from_payload(payload: dict, *, limit: int) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for item in payload.get("candidates", []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        candidates.append(
            MemoryCandidate(
                text=text,
                category=str(item.get("category", "other")).strip() or "other",
                confidence=float(item.get("confidence", 0.5)),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates
