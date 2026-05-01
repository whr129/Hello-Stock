import logging

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.agent.guardrails import enforce_financial_guardrails
from news_agent.agent.intent import IntentClassifier
from news_agent.agent.router import help_response, route_request
from news_agent.app.state import SupervisorState
from news_agent.domains.market.subagent import MarketSubagent
from news_agent.domains.news.subagent import NewsSubagent
from news_agent.markets.yahoo import YahooMarketDataProvider
from news_agent.memory.embeddings import EmbeddingService
from news_agent.memory.long_term import memory_from_user_text, should_store_memory
from news_agent.memory.short_term import append_message, expiry
from news_agent.search.service import GeneralSearchService
from news_agent.settings import Settings
from news_agent.storage.repositories import (
    EmbeddingRepository,
    MemoryRepository,
    PreferenceRepository,
    ShortTermSessionRepository,
    TickerRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)


def _next_step(state: SupervisorState) -> str:
    pending = state.get("pending_agents", [])
    if not pending:
        if _should_run_search(state):
            return "run_general_search"
        return "merge_agent_outputs"
    if pending[0] == "news":
        return "run_news_agent"
    return "run_market_agent"


def _should_run_search(state: SupervisorState) -> bool:
    if state.get("search_result", {}).get("response"):
        return False

    capabilities = set(state.get("route", {}).get("capabilities", []))
    if "general_search" in capabilities:
        return True

    news_meta = state.get("news_result", {}).get("metadata", {})
    market_meta = state.get("market_result", {}).get("metadata", {})
    return bool(news_meta.get("needs_search_fallback") or market_meta.get("needs_search_fallback"))


class SupervisorNodes:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.intent_classifier = IntentClassifier(settings)
        self.news_agent = NewsSubagent(session_factory, settings)
        self.market_agent = MarketSubagent(session_factory, settings, YahooMarketDataProvider())
        self.search_service = GeneralSearchService(settings)
        self.embedding_service = EmbeddingService(settings)

    async def load_user_context(self, state: SupervisorState) -> SupervisorState:
        async with self.session_factory() as session:
            user = await UserRepository(session, self.settings).get_or_create_user(
                state["telegram_user_id"]
            )
            preference = await PreferenceRepository(session).get_for_user(user.id)
            tickers = await TickerRepository(session).list_for_user(user.id)
            short_term_state = await ShortTermSessionRepository(session).get_state(state["chat_id"])
            memories = await MemoryRepository(session).list_for_user(user.id)
            await session.commit()

        return {
            **state,
            "errors": list(state.get("errors", [])),
            "metadata": dict(state.get("metadata", {})),
            "user_context": {
                "user_id": user.id,
                "local_region": user.local_region,
                "timezone": user.timezone,
                "topics": preference.topics,
                "watched_tickers": tickers,
                "short_term_memory": short_term_state,
                "long_term_memory": [memory.memory_text for memory in memories],
            },
        }

    async def classify_request(self, state: SupervisorState) -> SupervisorState:
        command, args, intent = await self.intent_classifier.classify(state.get("message_text", ""))
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
        return {
            **state,
            "route": {
                "agents": list(decision.agents),
                "capabilities": list(decision.capabilities),
                "fallback_response": decision.fallback_response,
            },
            "pending_agents": list(decision.agents),
            "completed_agents": [],
            "metadata": metadata,
        }

    async def run_news_agent(self, state: SupervisorState) -> SupervisorState:
        result = await self.news_agent.run(state)
        pending = [agent for agent in state.get("pending_agents", []) if agent != "news"]
        completed = list(state.get("completed_agents", [])) + ["news"]
        return {
            **state,
            "news_result": result,
            "pending_agents": pending,
            "completed_agents": completed,
        }

    async def run_market_agent(self, state: SupervisorState) -> SupervisorState:
        result = await self.market_agent.run(state)
        pending = [agent for agent in state.get("pending_agents", []) if agent != "market"]
        completed = list(state.get("completed_agents", [])) + ["market"]
        return {
            **state,
            "market_result": result,
            "pending_agents": pending,
            "completed_agents": completed,
        }

    async def run_general_search(self, state: SupervisorState) -> SupervisorState:
        result = await self.search_service.search(
            _build_search_query(state),
            state.get("user_context", {}),
        )
        return {
            **state,
            "search_result": {
                "response": result.answer,
                "metadata": {
                    **result.metadata,
                    "capability": "general_search",
                    "query": result.query,
                    "sources": [{"title": source.title, "url": source.url} for source in result.sources],
                },
            },
        }

    async def merge_agent_outputs(self, state: SupervisorState) -> SupervisorState:
        route = state.get("route", {})
        fallback_response = route.get("fallback_response")
        if fallback_response:
            response = fallback_response
        else:
            parts: list[str] = []
            news_response = state.get("news_result", {}).get("response")
            market_response = state.get("market_result", {}).get("response")
            search_response = state.get("search_result", {}).get("response")
            news_meta = state.get("news_result", {}).get("metadata", {})
            market_meta = state.get("market_result", {}).get("metadata", {})
            general_only = "general_search" in set(route.get("capabilities", []))
            if news_response:
                parts.append(news_response)
            if market_response:
                parts.append(market_response)
            if search_response:
                if general_only:
                    parts = [search_response]
                elif news_meta.get("needs_search_fallback") and market_meta.get("needs_search_fallback"):
                    parts.append(
                        "Fresh stored news and market snapshot data were unavailable, so this answer uses web search."
                    )
                    parts.append(search_response)
                elif news_meta.get("needs_search_fallback"):
                    parts.append(
                        "Fresh stored news data were unavailable, so the following answer uses web search."
                    )
                    parts.append(search_response)
                elif market_meta.get("needs_search_fallback"):
                    parts.append(
                        "Fresh market data were unavailable, so the following answer uses web search context."
                    )
                    parts.append(search_response)
            response = "\n\n".join(parts) if parts else help_response()

        return {**state, "final_response": response, "response": response}

    async def guardrail_check(self, state: SupervisorState) -> SupervisorState:
        response = state.get("final_response", "")
        capabilities = set(state.get("route", {}).get("capabilities", []))
        if state.get("market_result", {}).get("response") or (
            state.get("search_result", {}).get("response")
            and {"market_snapshot", "technical_analysis"} & capabilities
        ):
            response = enforce_financial_guardrails(response)
        return {**state, "final_response": response, "response": response}

    async def persist_session(self, state: SupervisorState) -> SupervisorState:
        text = state.get("message_text", "")
        response = state.get("final_response", "")
        short_term_state = dict(state.get("user_context", {}).get("short_term_memory", {}))
        append_message(short_term_state, "user", text)
        if response:
            append_message(short_term_state, "assistant", response)

        async with self.session_factory() as session:
            await ShortTermSessionRepository(session).save_state(
                state["chat_id"],
                short_term_state,
                expiry(),
            )
            await session.commit()

        if should_store_memory(text):
            async with self.session_factory() as session:
                memory = await MemoryRepository(session).remember(
                    user_id=state["user_context"]["user_id"],
                    text=memory_from_user_text(text),
                )
                embedding = await self.embedding_service.embed_text(memory.memory_text)
                await EmbeddingRepository(session).save_memory_embedding(
                    memory.id,
                    embedding,
                    self.settings.embedding_model,
                )
                await session.commit()

        user_context = dict(state.get("user_context", {}))
        user_context["short_term_memory"] = short_term_state
        return {**state, "user_context": user_context}


def build_supervisor_graph(session_factory: async_sessionmaker, settings: Settings):
    nodes = SupervisorNodes(session_factory, settings)
    graph = StateGraph(SupervisorState)

    graph.add_node("load_user_context", nodes.load_user_context)
    graph.add_node("classify_request", nodes.classify_request)
    graph.add_node("route_request", nodes.route_request)
    graph.add_node("run_news_agent", nodes.run_news_agent)
    graph.add_node("run_market_agent", nodes.run_market_agent)
    graph.add_node("run_general_search", nodes.run_general_search)
    graph.add_node("merge_agent_outputs", nodes.merge_agent_outputs)
    graph.add_node("guardrail_check", nodes.guardrail_check)
    graph.add_node("persist_session", nodes.persist_session)

    graph.set_entry_point("load_user_context")
    graph.add_edge("load_user_context", "classify_request")
    graph.add_edge("classify_request", "route_request")
    graph.add_conditional_edges(
        "route_request",
        _next_step,
        {
            "run_news_agent": "run_news_agent",
            "run_market_agent": "run_market_agent",
            "run_general_search": "run_general_search",
            "merge_agent_outputs": "merge_agent_outputs",
        },
    )
    graph.add_conditional_edges(
        "run_news_agent",
        _next_step,
        {
            "run_news_agent": "run_news_agent",
            "run_market_agent": "run_market_agent",
            "run_general_search": "run_general_search",
            "merge_agent_outputs": "merge_agent_outputs",
        },
    )
    graph.add_conditional_edges(
        "run_market_agent",
        _next_step,
        {
            "run_news_agent": "run_news_agent",
            "run_market_agent": "run_market_agent",
            "run_general_search": "run_general_search",
            "merge_agent_outputs": "merge_agent_outputs",
        },
    )
    graph.add_edge("run_general_search", "merge_agent_outputs")
    graph.add_edge("merge_agent_outputs", "guardrail_check")
    graph.add_edge("guardrail_check", "persist_session")
    graph.add_edge("persist_session", END)
    return graph.compile()


def _build_search_query(state: SupervisorState) -> str:
    message_text = state.get("message_text", "").strip()
    if message_text:
        return message_text

    requested_symbols = state.get("requested_symbols", [])
    if requested_symbols:
        return f"Latest performance and context for {' '.join(requested_symbols)}"

    return "Latest relevant information for the user request"
