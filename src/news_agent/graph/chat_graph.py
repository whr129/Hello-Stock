from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.app.supervisor import build_supervisor_graph
from news_agent.settings import Settings


def build_chat_graph(session_factory: async_sessionmaker, settings: Settings):
    return build_supervisor_graph(session_factory, settings)
