from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.graph.nodes import SchedulerNodes
from news_agent.graph.state import SchedulerState
from news_agent.settings import Settings


def build_scheduler_graph(session_factory: async_sessionmaker, settings: Settings):
    nodes = SchedulerNodes(session_factory, settings)
    graph = StateGraph(SchedulerState)

    graph.add_node("load_due_sources", nodes.traced("load_due_sources", nodes.load_due_sources))
    graph.add_node("fetch_parallel", nodes.traced("fetch_parallel", nodes.fetch_parallel))
    graph.add_node("normalize_dedupe", nodes.traced("normalize_dedupe", nodes.normalize_dedupe))
    graph.add_node("embed_store", nodes.traced("embed_store", nodes.embed_store))
    graph.add_node("precompute_summaries", nodes.traced("precompute_summaries", nodes.precompute_summaries))
    graph.add_node("quality_check", nodes.traced("quality_check", nodes.quality_check))
    graph.add_node("retry_or_recover", nodes.traced("retry_or_recover", nodes.retry_or_recover))

    graph.set_entry_point("load_due_sources")
    graph.add_edge("load_due_sources", "fetch_parallel")
    graph.add_edge("fetch_parallel", "normalize_dedupe")
    graph.add_edge("normalize_dedupe", "embed_store")
    graph.add_edge("embed_store", "precompute_summaries")
    graph.add_edge("precompute_summaries", "quality_check")
    graph.add_edge("quality_check", "retry_or_recover")
    graph.add_edge("retry_or_recover", END)

    return graph.compile()
