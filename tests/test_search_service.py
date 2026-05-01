from types import SimpleNamespace

import pytest

from news_agent.search.service import GeneralSearchService, SearchSource
from news_agent.settings import Settings


class FakeResponses:
    async def create(self, **kwargs):
        del kwargs
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

    result = await service.search("latest ai news", {"timezone": "America/Toronto"})

    assert result.metadata["status"] == "ok"
    assert "Sources:" in result.answer
    assert result.sources == [
        SearchSource(title="example.com", url="https://example.com/a"),
        SearchSource(title="example.com", url="https://example.com/b"),
    ]


@pytest.mark.asyncio
async def test_general_search_service_handles_missing_client() -> None:
    service = GeneralSearchService(Settings(openai_api_key=""))
    service.client = None

    result = await service.search("latest ai news")

    assert result.metadata["status"] == "unavailable"
    assert "unavailable" in result.answer.lower()
