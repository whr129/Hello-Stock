from news_agent.evaluation.runner import (
    EVAL_PROMPT_REVISION,
    EvalCase,
    _deterministic_judgment,
    _enforce_judgment_contract,
    _markdown_report,
)
from news_agent.llm_contracts import JudgeResponse


def _judge_response(**score_overrides: int) -> JudgeResponse:
    scores = {
        "relevance": 4,
        "specificity": 4,
        "ticker_correctness": 5,
        "theme_correctness": 5,
        "evidence_quality": 4,
        "freshness": 4,
        "source_attribution": 4,
        "source_link_validity": 5,
        "grounding": 5,
        "explainability": 4,
        "usefulness": 4,
        "safety": 5,
        "concision": 4,
    }
    scores.update(score_overrides)
    return JudgeResponse.model_validate(
        {"scores": scores, "pass": True, "tags": [], "notes": "Grounded answer."}
    )


def test_judge_contract_enforces_critical_score_threshold() -> None:
    judgment = _enforce_judgment_contract(_judge_response(grounding=3))

    assert judgment["pass"] is False
    assert judgment["evaluation_mode"] == "live_llm"


def test_deterministic_judgment_exposes_complete_rubric() -> None:
    judgment = _deterministic_judgment(
        EvalCase(id="basic", prompt="/research", expected="evidence and financial advice"),
        "Evidence: stored evidence. This is not financial advice.",
    )

    assert "specificity" in judgment["scores"]
    assert "grounding" in judgment["scores"]
    assert "explainability" in judgment["scores"]


def test_report_identifies_deterministic_fallback() -> None:
    result = {
        "id": "basic",
        "prompt": "/research",
        "judgment": {
            "pass": True,
            "tags": [],
            "notes": "fallback",
            "evaluation_mode": "deterministic_fallback",
        },
    }
    metadata = {
        "mode": "deterministic_fallback",
        "model": "none",
        "prompt_revision": EVAL_PROMPT_REVISION,
    }

    report = _markdown_report([result], metadata)

    assert "Evaluation mode: deterministic_fallback" in report
    assert f"Prompt revision: {EVAL_PROMPT_REVISION}" in report
    assert "Fallback judgments: 1" in report
    assert "Average relevance: 0.00" in report
