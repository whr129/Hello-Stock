import pytest

from news_agent.agent.intent import IntentClassifier
from news_agent.settings import Settings


@pytest.mark.asyncio
async def test_intent_classifier_uses_command_without_llm() -> None:
    classifier = IntentClassifier(Settings(openai_api_key=""))

    command, args, intent = await classifier.classify("/watch AAPL")

    assert command == "/watch"
    assert args == ["AAPL"]
    assert intent == "watch"


@pytest.mark.asyncio
async def test_intent_classifier_falls_back_to_general_chat_without_llm() -> None:
    classifier = IntentClassifier(Settings(openai_api_key=""))

    _, _, intent = await classifier.classify("hello there")

    assert intent == "general_chat"
