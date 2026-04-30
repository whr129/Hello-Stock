import pytest

from news_agent.agent.react import ReActResponder
from news_agent.settings import Settings


@pytest.mark.asyncio
async def test_react_responder_greets_without_llm() -> None:
    responder = ReActResponder(Settings(openai_api_key=""))

    result = await responder.respond("hi", {"tickers": [], "articles": [], "memories": []})

    assert result.action == "greet"
    assert "Hi" in result.answer
    assert "Thought" not in result.answer


@pytest.mark.asyncio
async def test_react_responder_explains_capabilities_without_llm() -> None:
    responder = ReActResponder(Settings(openai_api_key=""))

    result = await responder.respond("what can you do?", {})

    assert result.action == "explain_capabilities"
    assert "/brief" in result.answer
