import pytest

from news_agent.settings import Settings
from news_agent.summarizer.service import Summarizer, SummaryRequest


@pytest.mark.asyncio
async def test_summarizer_fallback_cites_source() -> None:
    summarizer = Summarizer(Settings(openai_api_key=""))

    result = await summarizer.summarize_article(
        SummaryRequest(
            title="Markets rise",
            text="Stocks moved higher after several companies reported earnings.",
            source="Example Feed",
        )
    )

    assert "Markets rise" in result
    assert "Example Feed" in result
