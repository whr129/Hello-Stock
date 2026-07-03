from news_agent.research.reporting import GUARDRAIL_TEXT, format_candidates, format_signal
from news_agent.research.schemas import CandidateExplanation


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
