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

    assert "MU - memory chips - score 78" in text
    assert "Components:" in text
    assert "HBM demand coverage accelerated." in text
    assert GUARDRAIL_TEXT in text


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
    assert "source diversity is limited" in text
    assert GUARDRAIL_TEXT in text
