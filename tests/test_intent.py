import pytest

from news_agent.agent.intent import IntentClassifier, ROUTER_SYSTEM_PROMPT
from news_agent.settings import Settings


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content

    async def create(self, **kwargs):
        return FakeResponse(self.content)


class FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = FakeCompletions(content)


class FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = FakeChat(content)


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


@pytest.mark.asyncio
async def test_intent_classifier_uses_llm_router_for_company_ticker() -> None:
    classifier = IntentClassifier(Settings(openai_api_key="test"))
    classifier.client = FakeClient('{"intent": "stocks", "args": ["GOOGL"]}')

    command, args, intent = await classifier.classify("give me price for Google")

    assert command == ""
    assert args == ["GOOGL"]
    assert intent == "stocks"


@pytest.mark.asyncio
async def test_intent_classifier_uses_llm_router_for_market_news() -> None:
    classifier = IntentClassifier(Settings(openai_api_key="test"))
    classifier.client = FakeClient('{"intent": "brief", "args": []}')

    _, args, intent = await classifier.classify("what happened to the stock market today")

    assert args == []
    assert intent == "brief"


@pytest.mark.asyncio
async def test_intent_classifier_uses_llm_router_for_mixed_request() -> None:
    classifier = IntentClassifier(Settings(openai_api_key="test"))
    classifier.client = FakeClient('{"intent": "brief", "args": ["NVDA"]}')

    _, args, intent = await classifier.classify("brief me on nvidia and today's ai news")

    assert args == ["NVDA"]
    assert intent == "brief"


@pytest.mark.asyncio
async def test_intent_classifier_uses_llm_router_for_runtime_request() -> None:
    classifier = IntentClassifier(Settings(openai_api_key="test"))
    classifier.client = FakeClient('{"intent": "runtime", "args": []}')

    _, args, intent = await classifier.classify("what happened in the last refresh?")

    assert args == []
    assert intent == "runtime"


def test_router_prompt_mentions_supported_outputs() -> None:
    assert '"intent": "brief" | "stocks" | "runtime" | "general_chat" | "help"' in ROUTER_SYSTEM_PROMPT
    assert "Return only valid JSON" in ROUTER_SYSTEM_PROMPT
