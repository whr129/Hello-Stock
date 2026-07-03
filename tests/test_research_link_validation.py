import pytest

from news_agent.research.link_validation import validate_candidate_links
from news_agent.research.schemas import CandidateExplanation


@pytest.mark.asyncio
async def test_validate_candidate_links_marks_available_urls() -> None:
    explanations = [
        CandidateExplanation(
            ticker="NVDA",
            theme="AI infrastructure",
            rank=1,
            total_score=86,
            components={},
            evidence=[
                {"article_url": "https://example.com/nvda", "source_name": "Nvidia"},
                {"article_url": "https://example.com/msft", "source_name": "Microsoft"},
            ],
            weak_evidence=[],
            evidence_strength="strong",
            distinct_source_count=2,
            linked_source_count=2,
            max_trust_score=0.9,
        )
    ]

    checked = await validate_candidate_links(explanations, checker=lambda url: True)

    assert checked[0].linked_source_count == 2
    assert checked[0].evidence_strength == "strong"
    assert {item["article_url_status"] for item in checked[0].evidence} == {"available"}


@pytest.mark.asyncio
async def test_validate_candidate_links_downgrades_failed_links() -> None:
    explanations = [
        CandidateExplanation(
            ticker="MU",
            theme="memory chips",
            rank=1,
            total_score=78,
            components={},
            evidence=[
                {"article_url": "https://example.com/hbm", "source_name": "Example Markets"}
            ],
            weak_evidence=[],
            evidence_strength="developing",
            distinct_source_count=1,
            linked_source_count=1,
            max_trust_score=0.8,
        )
    ]

    checked = await validate_candidate_links(explanations, checker=lambda url: False)

    assert checked[0].linked_source_count == 0
    assert checked[0].evidence_strength == "weak"
    assert checked[0].evidence[0]["article_url_status"] == "unavailable"
