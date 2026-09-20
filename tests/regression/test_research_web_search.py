import json
from types import SimpleNamespace

import pytest

from news_agent.research.web_search import (
    ResearchWebSearchService,
    canonicalize_public_url,
)
from news_agent.settings import Settings


class _FakeResponses:
    def __init__(self, payload: dict, sources: list[str]) -> None:
        self.payload = payload
        self.sources = sources
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text=json.dumps(self.payload),
            output=[
                SimpleNamespace(
                    type="web_search_call",
                    status="completed",
                    action=SimpleNamespace(
                        type="search",
                        sources=[SimpleNamespace(url=url) for url in self.sources],
                    ),
                )
            ],
        )


class _FakeClient:
    def __init__(self, payload: dict, sources: list[str]) -> None:
        self.responses = _FakeResponses(payload, sources)


def _payload() -> dict:
    return {
        "ticker": "NVDA",
        "company_name": "Nvidia",
        "identity_status": "matched",
        "overview": "Nvidia designs accelerated-computing platforms.",
        "overview_evidence_ids": ["sec"],
        "financial_period": "FY2026 Q1",
        "financial_facts": [
            {
                "metric": "Revenue",
                "value": "44.1",
                "unit": "billion",
                "currency": "USD",
                "period_end": "2026-04-26",
                "comparison_basis": "reported period",
                "evidence_ids": ["sec"],
            }
        ],
        "developments": [{"text": "Filed its quarterly report.", "evidence_ids": ["sec"]}],
        "catalysts": [],
        "risks": [{"text": "Export controls remain a risk.", "evidence_ids": ["news"]}],
        "contradictions": [],
        "missing_checks": [],
        "evidence": [
            {
                "id": "sec",
                "url": "https://www.sec.gov/ixviewer/doc?utm_source=test",
                "title": "Quarterly report",
                "published_at": "2026-05-20T00:00:00Z",
                "event_at": None,
                "summary": "The filing reports quarterly results.",
                "source_kind": "filing",
                "form_type": "10-Q",
                "fiscal_period": "FY2026 Q1",
            },
            {
                "id": "news",
                "url": "https://example.com/nvda-risk",
                "title": "Export control update",
                "published_at": "2026-05-21T00:00:00Z",
                "event_at": None,
                "summary": "The report discusses export controls.",
                "source_kind": "news",
                "form_type": None,
                "fiscal_period": None,
            },
            {
                "id": "invented",
                "url": "https://invented.example/fake",
                "title": "Invented",
                "published_at": None,
                "event_at": None,
                "summary": "Unsupported.",
                "source_kind": "analysis",
                "form_type": None,
                "fiscal_period": None,
            },
        ],
        "confidence": 0.88,
    }


@pytest.mark.asyncio
async def test_research_web_search_uses_stable_tool_and_keeps_discovered_sources_only() -> None:
    payload = _payload()
    client = _FakeClient(
        payload,
        [
            "https://www.sec.gov/ixviewer/doc",
            "https://example.com/nvda-risk",
        ],
    )
    service = ResearchWebSearchService(
        Settings(openai_api_key="test", research_web_enabled=True),
        client=client,
    )

    packet = await service.research_company(
        ticker="NVDA",
        company_name="Nvidia",
        theme="AI infrastructure",
        query="latest financials",
        horizon="30d",
    )

    assert client.responses.kwargs["tools"] == [
        {"type": "web_search", "search_context_size": "medium"}
    ]
    assert client.responses.kwargs["store"] is False
    assert client.responses.kwargs["text"]["format"]["type"] == "json_schema"
    assert [item.id for item in packet.evidence] == ["sec", "news"]
    assert packet.evidence[0].source_tier == 1
    assert packet.status == "partial"
    assert "unsupported_sources_removed" in packet.errors
    request_input = client.responses.kwargs["input"]
    assert "Telegram" not in request_input
    assert "API_KEY" not in request_input


@pytest.mark.asyncio
async def test_research_web_search_rejects_ticker_mismatch() -> None:
    payload = _payload()
    payload["ticker"] = "AMD"
    service = ResearchWebSearchService(
        Settings(openai_api_key="test", research_web_enabled=True),
        client=_FakeClient(payload, ["https://www.sec.gov/ixviewer/doc"]),
    )

    packet = await service.research_company(
        ticker="NVDA",
        company_name="Nvidia",
        theme="AI infrastructure",
        query="latest",
        horizon="30d",
    )

    assert packet.status == "failed"
    assert packet.errors == ["identity_mismatch"]
    assert not packet.evidence


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a",
        "http://localhost/a",
        "http://127.0.0.1/a",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/a",
        "https://user:pass@example.com/a",
    ],
)
def test_canonicalize_public_url_rejects_unsafe_targets(url: str) -> None:
    assert canonicalize_public_url(url) == ""


def test_canonicalize_public_url_removes_tracking_and_fragment() -> None:
    assert canonicalize_public_url(
        "HTTPS://Example.COM:443/a?utm_source=x&keep=1#section"
    ) == "https://example.com/a?keep=1"
