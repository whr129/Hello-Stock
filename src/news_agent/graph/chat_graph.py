from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.graph.nodes import GraphNodes
from news_agent.graph.state import NewsAgentState
from news_agent.settings import Settings


def _after_command(state: NewsAgentState) -> str:
    if state.get("response"):
        return "guardrail_check"
    return "retrieve_context"


def build_chat_graph(session_factory: async_sessionmaker, settings: Settings):
    nodes = GraphNodes(session_factory, settings)
    graph = StateGraph(NewsAgentState)

    graph.add_node("parse_intent", nodes.parse_intent)
    graph.add_node("load_user_state", nodes.load_user_state)
    graph.add_node("apply_command", nodes.apply_command)
    graph.add_node("retrieve_context", nodes.retrieve_context)
    graph.add_node("rank_context", nodes.rank_context)
    graph.add_node("generate_response", nodes.generate_response)
    graph.add_node("guardrail_check", nodes.guardrail_check)
    graph.add_node("persist_memory", nodes.persist_memory)

    graph.set_entry_point("parse_intent")
    graph.add_edge("parse_intent", "load_user_state")
    graph.add_edge("load_user_state", "apply_command")
    graph.add_conditional_edges(
        "apply_command",
        _after_command,
        {
            "retrieve_context": "retrieve_context",
            "guardrail_check": "guardrail_check",
        },
    )
    graph.add_edge("retrieve_context", "rank_context")
    graph.add_edge("rank_context", "generate_response")
    graph.add_edge("generate_response", "guardrail_check")
    graph.add_edge("guardrail_check", "persist_memory")
    graph.add_edge("persist_memory", END)

    return graph.compile()
