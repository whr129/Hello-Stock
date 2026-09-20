from datetime import UTC, datetime

from news_agent.research.reporting import (
    GUARDRAIL_TEXT,
    format_candidates,
    format_signal,
    format_source_health,
)
from news_agent.research.schemas import (
    CandidateExplanation,
    CompanyResearchPacket,
    ResearchClaim,
    ResearchFinancialFact,
    ResearchWebEvidence,
)
from news_agent.storage.models import Source


def test_candidate_report_includes_components_evidence_and_guardrail() -> None:
    text = format_candidates(
        [
            CandidateExplanation(
                ticker="MU",
                theme="memory chips",
                rank=1,
                total_score=78,
                components={"mention_velocity": 80, "source_diversity": 40},
                evidence=[{"text": "HBM demand coverage accelerated.", "source_family": "news"}],
                weak_evidence=["filings catalyst not confirmed"],
            )
        ]
    )

    assert "MU - memory chips - score 78 - weak evidence" in text
    assert "Why this ranked:" in text
    assert "Score drivers:" in text
    assert "Source quality:" in text
    assert "HBM demand coverage accelerated." in text
    assert "Stored evidence, link unavailable" in text
    assert GUARDRAIL_TEXT in text


def test_candidate_report_includes_validated_source_links_when_available() -> None:
    text = format_candidates(
        [
            CandidateExplanation(
                ticker="MU",
                theme="memory chips",
                rank=1,
                total_score=78,
                components={"mention_velocity": 80, "source_diversity": 60},
                evidence=[
                    {
                        "article_title": "HBM demand accelerates",
                        "article_url": "https://example.com/hbm",
                        "article_url_status": "available",
                        "source_name": "Example Markets",
                        "published_at": "2026-05-23T12:00:00+00:00",
                        "evidence_text": "HBM demand coverage accelerated.",
                        "trust_score": 0.8,
                    }
                ],
                weak_evidence=[],
                evidence_strength="developing",
                distinct_source_count=1,
                linked_source_count=1,
                max_trust_score=0.8,
            )
        ]
    )

    assert "HBM demand accelerates - Example Markets, 2026-05-23" in text
    assert "https://example.com/hbm" in text
    assert "Why it matters:" in text
    assert "HBM demand coverage accelerated." in text


def test_candidate_report_respects_configured_evidence_limit() -> None:
    text = format_candidates(
        [
            CandidateExplanation(
                ticker="NVDA",
                theme="AI infrastructure",
                rank=1,
                total_score=90,
                components={},
                evidence=[
                    {
                        "article_title": "First source",
                        "article_url": "https://example.com/1",
                        "article_url_status": "available",
                    },
                    {
                        "article_title": "Second source",
                        "article_url": "https://example.com/2",
                        "article_url_status": "available",
                    },
                ],
                weak_evidence=[],
            )
        ],
        max_evidence_items=1,
    )

    assert "First source" in text
    assert "Second source" not in text


def test_candidate_report_does_not_print_unchecked_or_failed_urls_as_links() -> None:
    text = format_candidates(
        [
            CandidateExplanation(
                ticker="MU",
                theme="memory chips",
                rank=1,
                total_score=78,
                components={"mention_velocity": 80},
                evidence=[
                    {
                        "article_title": "Unchecked HBM demand",
                        "article_url": "https://example.com/unchecked",
                        "source_name": "Example Markets",
                    },
                    {
                        "article_title": "Failed HBM demand",
                        "article_url": "https://example.com/failed",
                        "article_url_status": "unavailable",
                        "source_name": "Example Markets",
                    },
                ],
                weak_evidence=[],
            )
        ]
    )

    assert "https://example.com/unchecked" not in text
    assert "https://example.com/failed" not in text
    assert "link not checked yet" in text
    assert "link unavailable after validation" in text
    assert text.count("Why it matters:") == 2


def test_signal_report_includes_weak_evidence_and_guardrail() -> None:
    text = format_signal(
        [
            CandidateExplanation(
                ticker="MU",
                theme=None,
                rank=2,
                total_score=60,
                components={"mention_velocity": 70},
                evidence=[],
                weak_evidence=["source diversity is limited"],
            )
        ],
        "MU",
    )

    assert "MU signal explanation" in text
    assert "Evidence chain: not enough stored evidence yet." in text
    assert "source diversity is limited" in text
    assert GUARDRAIL_TEXT in text


def test_signal_report_includes_score_movement() -> None:
    text = format_signal(
        [
            CandidateExplanation(
                ticker="NVDA",
                theme="AI infrastructure",
                rank=1,
                total_score=80,
                components={},
                evidence=[],
                weak_evidence=[],
            ),
            CandidateExplanation(
                ticker="NVDA",
                theme="AI infrastructure",
                rank=1,
                total_score=70,
                components={},
                evidence=[],
                weak_evidence=[],
            ),
        ],
        "NVDA",
    )

    assert "Score movement: up 10 points" in text


def test_candidate_report_adds_cited_live_research_without_changing_score() -> None:
    evidence = ResearchWebEvidence(
        id="sec",
        ticker="NVDA",
        title="Nvidia quarterly report",
        publisher="sec.gov",
        url="https://www.sec.gov/example",
        canonical_url="https://www.sec.gov/example",
        source_kind="filing",
        source_tier=1,
        trust_score=0.99,
        primary=True,
        published_at=datetime(2026, 5, 20, tzinfo=UTC),
        event_at=None,
        retrieved_at=datetime(2026, 5, 21, tzinfo=UTC),
        summary="Quarterly results.",
    )
    packet = CompanyResearchPacket(
        ticker="NVDA",
        company_name="Nvidia",
        identity_status="matched",
        as_of=datetime(2026, 5, 21, tzinfo=UTC),
        status="complete",
        overview="Nvidia designs accelerated-computing platforms.",
        overview_evidence_ids=["sec"],
        financial_period="FY2026 Q1",
        financial_facts=[
            ResearchFinancialFact(
                metric="Revenue",
                value="44.1",
                unit="billion",
                currency="USD",
                period_end="2026-04-26",
                comparison_basis="reported period",
                evidence_ids=["sec"],
            )
        ],
        risks=[ResearchClaim(text="Export controls remain a risk.", evidence_ids=["sec"])],
        evidence=[evidence],
        confidence=0.9,
    )
    candidate = CandidateExplanation(
        ticker="NVDA",
        theme="AI infrastructure",
        rank=1,
        total_score=88,
        components={},
        evidence=[],
        weak_evidence=[],
    )

    text = format_candidates([candidate], company_research={"NVDA": packet})

    assert "score 88" in text
    assert "does not change the stored score" in text
    assert "Revenue: 44.1 USD billion" in text
    assert "Export controls remain a risk. [sec]" in text
    assert "https://www.sec.gov/example" in text
    assert "Research priority:" in text


def test_source_health_report_lists_status_and_score() -> None:
    source = Source(
        name="Example Feed",
        provider="rss",
        url="https://example.com/feed.xml",
        external_account="https://example.com/feed.xml",
        enabled=True,
        config={"source_health": {"fetched": 10, "accepted": 8, "saved": 5}},
    )

    text = format_source_health([source])

    assert "Research source health" in text
    assert "Example Feed" in text
    assert "score" in text
