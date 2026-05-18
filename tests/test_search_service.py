from types import SimpleNamespace

import pytest

from news_agent.search.service import GeneralSearchService, SearchSource
from news_agent.settings import Settings


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text="Current answer.",
            output=[
                SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        sources=[
                            SimpleNamespace(url="https://example.com/a"),
                            SimpleNamespace(url="https://example.com/b"),
                        ]
                    ),
                )
            ],
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


@pytest.mark.asyncio
async def test_general_search_service_formats_answer_with_sources() -> None:
    service = GeneralSearchService(Settings(openai_api_key="test"))
    service.client = FakeClient()

    result = await service.search("latest ai news", {})

    assert result.metadata["status"] == "ok"
    assert "Sources:" in result.answer
    assert result.sources == [
        SearchSource(title="example.com", url="https://example.com/a"),
        SearchSource(title="example.com", url="https://example.com/b"),
    ]


@pytest.mark.asyncio
async def test_general_search_service_includes_trusted_user_context() -> None:
    service = GeneralSearchService(Settings(openai_api_key="test"))
    fake_client = FakeClient()
    service.client = fake_client

    await service.search(
        "what is my name?",
        {
            "long_term_memory": ["User prefers English replies."],
            "short_term_memory": {
                "messages": [
                    {"type": "human", "data": {"content": "ok call me Howard"}},
                    {"type": "ai", "data": {"content": "Got it, Howard!"}},
                ]
            },
        },
    )

    request_input = fake_client.responses.kwargs["input"]
    assert "Trusted bot context:" in request_input
    assert "Local region: toronto" not in request_input
    assert "User prefers English replies." in request_input
    assert "ok call me Howard" in request_input
    assert "User question:\nwhat is my name?" in request_input


@pytest.mark.asyncio
async def test_general_search_service_handles_missing_client() -> None:
    service = GeneralSearchService(Settings(openai_api_key=""))
    service.client = None

    result = await service.search("latest ai news")

    assert result.metadata["status"] == "unavailable"
    assert "unavailable" in result.answer.lower()
