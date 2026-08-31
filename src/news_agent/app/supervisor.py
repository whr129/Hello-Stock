import logging
from collections.abc import Awaitable, Callable

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.agent.guardrails import enforce_financial_guardrails
from news_agent.agent.main_agent import MainAgent
from news_agent.agent.reflection import ReflectionService
from news_agent.agent.router import parse_message, route_request
from news_agent.agent.tools import build_main_tools
from news_agent.app.state import SupervisorState
from news_agent.domains.news.subagent import NewsSubagent
from news_agent.domains.runtime.subagent import RuntimeSubagent
from news_agent.memory.consolidation import MemoryConsolidationService
from news_agent.memory.embeddings import EmbeddingService
from news_agent.memory.short_term import (
    append_message,
    deserialize_state,
    expiry,
    serialize_state,
)
from news_agent.observability.runtime import (
    RuntimeAlertService,
    RuntimeTraceService,
    summarize_run_state,
)
from news_agent.research.agents import ResearchSubagent
from news_agent.settings import Settings
from news_agent.storage.repositories import (
    ConversationEventRepository,
    MemoryRepository,
    ShortTermSessionRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)

REFLECTION_EXHAUSTED_NOTE = (
    "Note: I could not confidently repair this answer after a reflection retry. "
    "You may want to rephrase or use a direct command such as /research, /candidates, or /runtime."
)


def _route_decision(state: SupervisorState) -> str:
    """Known commands dispatch to their subagent; everything else uses the main agent."""
    if state.get("command") and state.get("route", {}).get("fallback_response") is None:
        for agent in state.get("route", {}).get("agents", []):
            if agent == "news":
                return "run_news_agent"
            if agent == "runtime":
                return "run_runtime_agent"
            if agent == "research":
                return "run_research_agent"
    return "run_main_agent"


def _after_reflection(state: SupervisorState) -> str:
    if not state.get("metadata", {}).get("reflection_retry"):
        return "persist_session"
    corrected = state.get("reflection_decision", {}).get("corrected_agent")
    if corrected == "research":
        return "run_research_agent"
    if corrected == "runtime":
        return "run_runtime_agent"
    return "run_main_agent"


class SupervisorNodes:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.news_agent = NewsSubagent(session_factory, settings)
        self.runtime_agent = RuntimeSubagent(session_factory, settings)
        self.research_agent = ResearchSubagent(session_factory, settings)
        self.main_agent = MainAgent(settings)
        self.tools = build_main_tools(
            session_factory,
            settings,
            research_agent=self.research_agent,
            runtime_agent=self.runtime_agent,
        )
        self.reflection_service = ReflectionService(settings)
        self.embedding_service = EmbeddingService(settings)
        self.memory_service = MemoryConsolidationService(session_factory, settings)
        self.trace_service = RuntimeTraceService(session_factory, settings)
        self.alert_service = RuntimeAlertService(session_factory, settings)

    async def load_user_context(self, state: SupervisorState) -> SupervisorState:
        query_embedding = await self.embedding_service.embed_text(state.get("message_text", ""))
        async with self.session_factory() as session:
            user = await UserRepository(session, self.settings).get_or_create_user(
                state["telegram_user_id"]
            )
            stored_state = await ShortTermSessionRepository(session).get_state(state["chat_id"])
            short_term_state = deserialize_state(stored_state)
            memories = await MemoryRepository(session).semantic_search_for_user(
                user_id=user.id,
                query_embedding=query_embedding,
                limit=self.settings.long_term_memory_top_k,
            )
            if not memories:
                memories = await MemoryRepository(session).list_for_user(user.id)
            await session.commit()

        return {
            **state,
            "errors": list(state.get("errors", [])),
            "metadata": dict(state.get("metadata", {})),
            "messages": list(short_term_state.get("messages", [])),
            "user_context": {
                "user_id": user.id,
                "short_term_memory": serialize_state(
                    short_term_state,
                    max_messages=self.settings.short_term_memory_window_size,
                ),
                "long_term_memory": [memory.memory_text for memory in memories],
            },
        }

    async def classify_request(self, state: SupervisorState) -> SupervisorState:
        command, args, intent = parse_message(state.get("message_text", ""))
        requested_symbols = state.get("requested_symbols", [])
        if not requested_symbols:
            requested_symbols = [
                item.upper() for item in args if item.isalpha() and 1 <= len(item) <= 5
            ]

        return {
            **state,
            "command": command,
            "args": args,
            "intent": intent,
            "requested_symbols": requested_symbols,
        }

    async def route_request(self, state: SupervisorState) -> SupervisorState:
        decision = route_request(
            intent=state.get("intent", "unknown"),
            message_text=state.get("message_text", ""),
            command=state.get("command", ""),
            args=state.get("args", []),
        )
        metadata = dict(state.get("metadata", {}))
        metadata["route_agents"] = list(decision.agents)
        metadata["route_capabilities"] = list(decision.capabilities)
        use_main_agent = not decision.agents or decision.fallback_response is not None
        return {
            **state,
            "route": {
                "agents": list(decision.agents),
                "capabilities": list(decision.capabilities),
                "fallback_response": None if use_main_agent else decision.fallback_response,
            },
            "metadata": metadata,
        }

    async def run_news_agent(self, state: SupervisorState) -> SupervisorState:
        result = await self.news_agent.run(state)
        return {
            **state,
            "news_result": result,
            "final_response": result["response"],
            "response": result["response"],
        }

    async def run_runtime_agent(self, state: SupervisorState) -> SupervisorState:
        result = await self.runtime_agent.run(state)
        return {
            **state,
            "runtime_result": result,
            "final_response": result["response"],
            "response": result["response"],
        }

    async def run_research_agent(self, state: SupervisorState) -> SupervisorState:
        result = await self.research_agent.run(state)
        return {
            **state,
            "research_result": result,
            "final_response": result["response"],
            "response": result["response"],
        }

    async def run_main_agent(self, state: SupervisorState) -> SupervisorState:
        retry_hint = str(
            state.get("reflection_decision", {}).get("retry_hint", "")
            if state.get("metadata", {}).get("reflection_retry")
            else ""
        )
        answer, tool_log = await self.main_agent.run(
            message_text=state.get("message_text", ""),
            user_context=state.get("user_context", {}),
            tools=self.tools,
            retry_hint=retry_hint,
        )
        metadata = dict(state.get("metadata", {}))
        metadata["main_agent_tool_calls"] = tool_log
        return {
            **state,
            "main_agent_result": {
                "response": answer,
                "metadata": {"capability": "main_agent"},
            },
            "final_response": answer,
            "response": answer,
            "metadata": metadata,
        }

    async def guardrail_check(self, state: SupervisorState) -> SupervisorState:
        response = state.get("final_response", "")
        if state.get("research_result", {}).get("response"):
            response = enforce_financial_guardrails(response)
        elif state.get("main_agent_result", {}).get("response"):
            response = enforce_financial_guardrails(response)
        return {**state, "final_response": response, "response": response}

    async def reflect_result(self, state: SupervisorState) -> SupervisorState:
        metadata = dict(state.get("metadata", {}))
        metadata.pop("reflection_retry", None)
        reflection_notes = list(state.get("reflection_notes", []))
        attempts = int(state.get("reflection_attempts", 0) or 0)

        if not self.settings.answer_reflection_enabled:
            return {
                **state,
                "metadata": {**metadata, "reflection_status": "disabled"},
                "reflection_attempts": attempts,
                "reflection_notes": reflection_notes,
            }

        decision = await self.reflection_service.reflect(state)
        decision_payload = {
            "verdict": decision.verdict,
            "reason": decision.reason,
            "corrected_agent": decision.corrected_agent,
            "retry_hint": decision.retry_hint,
            "status": decision.status,
            "attempt": attempts,
        }
        metadata["reflection_decision"] = decision_payload
        metadata["reflection_status"] = decision.status

        if decision.verdict == "pass":
            return {
                **state,
                "metadata": metadata,
                "reflection_attempts": attempts,
                "reflection_decision": decision_payload,
                "reflection_notes": reflection_notes,
            }

        corrected_agent = decision.corrected_agent
        retry_hint = str(decision.retry_hint or "")
        if decision.verdict == "retry" and attempts < self.settings.answer_reflection_max_retries:
            metadata["reflection_retry"] = True
            metadata.setdefault("reflection_history", []).append(decision_payload)
            command, args = "", []
            if corrected_agent == "research":
                command = "/research"
            elif corrected_agent == "runtime":
                command = "/runtime"
            return {
                **state,
                "command": command,
                "args": args,
                "news_result": {},
                "runtime_result": {},
                "research_result": {},
                "main_agent_result": {},
                "final_response": "",
                "response": "",
                "metadata": metadata,
                "reflection_attempts": attempts + 1,
                "reflection_decision": {
                    **decision_payload,
                    "corrected_agent": corrected_agent,
                    "retry_hint": retry_hint,
                },
                "reflection_notes": reflection_notes + [decision.reason],
                "reflection_exhausted": False,
            }

        response = state.get("final_response", "")
        if REFLECTION_EXHAUSTED_NOTE not in response:
            response = f"{response}\n\n{REFLECTION_EXHAUSTED_NOTE}".strip()
        metadata["reflection_exhausted"] = True
        metadata.setdefault("reflection_history", []).append(decision_payload)
        return {
            **state,
            "final_response": response,
            "response": response,
            "metadata": metadata,
            "reflection_attempts": attempts,
            "reflection_decision": decision_payload,
            "reflection_notes": reflection_notes + [decision.reason],
            "reflection_exhausted": True,
        }

    async def persist_session(self, state: SupervisorState) -> SupervisorState:
        text = state.get("message_text", "")
        response = state.get("final_response", "")
        short_term_state = {"messages": list(state.get("messages", []))}
        append_message(
            short_term_state,
            "user",
            text,
            max_messages=self.settings.short_term_memory_window_size,
        )
        if response:
            append_message(
                short_term_state,
                "assistant",
                response,
                max_messages=self.settings.short_term_memory_window_size,
            )

        async with self.session_factory() as session:
            await ShortTermSessionRepository(session).save_state(
                state["chat_id"],
                serialize_state(
                    short_term_state,
                    max_messages=self.settings.short_term_memory_window_size,
                ),
                expiry(self.settings.short_term_memory_expiry_minutes),
            )
            event_repo = ConversationEventRepository(session)
            await event_repo.create(
                user_id=state["user_context"]["user_id"],
                chat_id=state["chat_id"],
                role="user",
                content=text,
                metadata={"intent": state.get("intent", ""), "command": state.get("command", "")},
            )
            if response:
                await event_repo.create(
                    user_id=state["user_context"]["user_id"],
                    chat_id=state["chat_id"],
                    role="assistant",
                    content=response,
                    metadata={"capabilities": state.get("route", {}).get("capabilities", [])},
                )
            await session.commit()

        await self.memory_service.enqueue_if_due(user_id=state["user_context"]["user_id"])

        user_context = dict(state.get("user_context", {}))
        user_context["short_term_memory"] = serialize_state(
            short_term_state,
            max_messages=self.settings.short_term_memory_window_size,
        )
        return {
            **state,
            "messages": list(short_term_state.get("messages", [])),
            "user_context": user_context,
        }

    def traced(
        self,
        step_name: str,
        func: Callable[[SupervisorState], Awaitable[SupervisorState]],
        *,
        step_type: str = "node",
        finalize_run: bool = False,
    ) -> Callable[[SupervisorState], Awaitable[SupervisorState]]:
        async def wrapped(state: SupervisorState) -> SupervisorState:
            run_id = await self.trace_service.ensure_run(
                workflow="chat",
                trigger=_workflow_trigger(state),
                telegram_user_id=state.get("telegram_user_id"),
                chat_id=state.get("chat_id"),
                metadata=_state_metadata(state),
                run_id=state.get("runtime_run_id"),
            )
            parent_step_id = state.get("active_step_id")
            step_id = await self.trace_service.start_step(
                run_id=run_id,
                workflow="chat",
                step_name=step_name,
                step_type=step_type,
                parent_step_id=parent_step_id,
                metadata=_step_metadata(state),
            )
            state = {**state, "runtime_run_id": run_id, "active_step_id": step_id}
            try:
                result = await func(state)
            except Exception as exc:
                message = str(exc)
                await self.trace_service.finish_step(
                    step_id,
                    status="failed",
                    error_message=message,
                )
                error_id = await self.trace_service.record_error(
                    run_id=run_id,
                    workflow="chat",
                    step_name=step_name,
                    error_message=message,
                    step_id=step_id,
                    metadata=_step_metadata(state),
                )
                await self.trace_service.finish_run(run_id, status="failed", summary=message[:500])
                await self.alert_service.send_alert(
                    run_id=run_id,
                    error_id=error_id,
                    message_text=(
                        f"Runtime alert\n"
                        f"- Workflow: chat\n"
                        f"- Run: {run_id}\n"
                        f"- Step: {step_name}\n"
                        f"- Error: {message}"
                    ),
                )
                raise

            result = {**result, "runtime_run_id": run_id, "active_step_id": parent_step_id}
            await self.trace_service.finish_step(
                step_id,
                status="completed",
                metadata=_step_metadata(result),
            )
            if finalize_run:
                status = (
                    "completed_with_errors"
                    if result.get("errors") or result.get("reflection_exhausted")
                    else "completed"
                )
                await self.trace_service.finish_run(
                    run_id,
                    status=status,
                    summary=summarize_run_state("chat", result),
                )
                if result.get("errors"):
                    for error in result["errors"]:
                        error_id = await self.trace_service.record_error(
                            run_id=run_id,
                            workflow="chat",
                            step_name=step_name,
                            error_message=str(error),
                            step_id=step_id,
                        )
                        await self.alert_service.send_alert(
                            run_id=run_id,
                            error_id=error_id,
                            message_text=(
                                f"Runtime alert\n"
                                f"- Workflow: chat\n"
                                f"- Run: {run_id}\n"
                                f"- Step: {step_name}\n"
                                f"- Error: {error}"
                            ),
                        )
            return result

        return wrapped


def build_supervisor_graph(session_factory: async_sessionmaker, settings: Settings):
    nodes = SupervisorNodes(session_factory, settings)
    graph = StateGraph(SupervisorState)
    research_handler = getattr(nodes, "run_research_agent", None)

    async def fallback_research_agent(state: SupervisorState) -> SupervisorState:
        response = "Market research is unavailable in this graph configuration."
        return {
            **state,
            "research_result": {
                "response": response,
                "metadata": {"capability": "market_research", "status": "unavailable"},
            },
            "final_response": response,
            "response": response,
        }

    graph.add_node("load_user_context", nodes.traced("load_user_context", nodes.load_user_context))
    graph.add_node("classify_request", nodes.traced("classify_request", nodes.classify_request))
    graph.add_node("route_request", nodes.traced("route_request", nodes.route_request))
    graph.add_node(
        "run_news_agent",
        nodes.traced("run_news_agent", nodes.run_news_agent, step_type="subagent"),
    )
    graph.add_node(
        "run_runtime_agent",
        nodes.traced("run_runtime_agent", nodes.run_runtime_agent, step_type="subagent"),
    )
    graph.add_node(
        "run_research_agent",
        nodes.traced(
            "run_research_agent",
            research_handler or fallback_research_agent,
            step_type="subagent",
        ),
    )
    graph.add_node(
        "run_main_agent",
        nodes.traced("run_main_agent", nodes.run_main_agent, step_type="subagent"),
    )
    graph.add_node("guardrail_check", nodes.traced("guardrail_check", nodes.guardrail_check))
    graph.add_node(
        "reflect_result",
        nodes.traced("reflect_result", nodes.reflect_result, step_type="tool"),
    )
    graph.add_node(
        "persist_session",
        nodes.traced("persist_session", nodes.persist_session, finalize_run=True),
    )

    graph.set_entry_point("load_user_context")
    graph.add_edge("load_user_context", "classify_request")
    graph.add_edge("classify_request", "route_request")
    command_targets = {
        "run_news_agent": "run_news_agent",
        "run_runtime_agent": "run_runtime_agent",
        "run_research_agent": "run_research_agent",
        "run_main_agent": "run_main_agent",
    }
    graph.add_conditional_edges("route_request", _route_decision, command_targets)
    for source in ("run_news_agent", "run_runtime_agent", "run_research_agent", "run_main_agent"):
        graph.add_edge(source, "guardrail_check")
    graph.add_edge("guardrail_check", "reflect_result")
    graph.add_conditional_edges(
        "reflect_result",
        _after_reflection,
        {
            "run_main_agent": "run_main_agent",
            "run_research_agent": "run_research_agent",
            "run_runtime_agent": "run_runtime_agent",
            "persist_session": "persist_session",
        },
    )
    graph.add_edge("persist_session", END)
    return graph.compile()

def _workflow_trigger(state: SupervisorState) -> str | None:
    return state.get("command") or state.get("intent")


def _state_metadata(state: SupervisorState) -> dict[str, object]:
    return {
        "message_text": state.get("message_text", "")[:500],
        "intent": state.get("intent", ""),
        "command": state.get("command", ""),
    }


def _step_metadata(state: SupervisorState) -> dict[str, object]:
    return {
        "route_capabilities": list(state.get("route", {}).get("capabilities", [])),
        "main_agent_tool_calls": list(
            state.get("metadata", {}).get("main_agent_tool_calls", [])
        ),
        "reflection_attempts": int(state.get("reflection_attempts", 0) or 0),
        "reflection_decision": dict(state.get("reflection_decision", {})),
        "reflection_exhausted": bool(state.get("reflection_exhausted", False)),
    }
