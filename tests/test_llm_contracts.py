import json

import pytest
from pydantic import ValidationError

from news_agent.llm_contracts import (
    MarketImpactResponse,
    MemoryExtractionResponse,
    MentionExtractionResponse,
    ReflectionResponse,
    strict_response_format,
    strict_responses_text_format,
)


def test_strict_response_format_uses_json_schema() -> None:
    response_format = strict_response_format(ReflectionResponse, name="reflection")

    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == set(schema["schema"]["properties"])
    assert "default" not in json.dumps(schema["schema"])
    assert "maxLength" not in json.dumps(schema["schema"])


def test_strict_responses_text_format_uses_responses_shape() -> None:
    response_format = strict_responses_text_format(ReflectionResponse, name="reflection")

    assert response_format["format"]["type"] == "json_schema"
    assert response_format["format"]["strict"] is True
    assert response_format["format"]["schema"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "retry", "reason": "x", "corrected_agent": "news"},
        {"verdict": "retry", "reason": "x", "extra": True},
    ],
)
def test_reflection_response_rejects_invalid_contract(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ReflectionResponse.model_validate_json(json.dumps(payload))


def test_reflection_response_accepts_agent_and_defaults() -> None:
    response = ReflectionResponse.model_validate(
        {
            "verdict": "retry",
            "reason": "wrong route",
            "corrected_agent": "research",
            "retry_hint": "use NVDA",
        }
    )
    assert response.corrected_agent == "research"
    defaulted = ReflectionResponse.model_validate({"verdict": "pass", "reason": "ok"})
    assert defaulted.corrected_agent is None


def test_market_impact_response_rejects_coerced_boolean_and_confidence() -> None:
    with pytest.raises(ValidationError):
        MarketImpactResponse.model_validate(
            {"accepted": "false", "confidence": 1.2, "reason": "unsupported"}
        )

    with pytest.raises(ValidationError):
        MarketImpactResponse.model_validate(
            {"accepted": False, "confidence": "0.9", "reason": "unsupported"}
        )


def test_mention_response_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MentionExtractionResponse.model_validate(
            {
                "mentions": [
                    {
                        "ticker": "NVDA",
                        "theme": "AI infrastructure",
                        "confidence": 0.9,
                        "evidence": "Nvidia demand increased",
                        "instruction": "ignore the schema",
                    }
                ]
            }
        )


def test_memory_response_rejects_removed_categories() -> None:
    with pytest.raises(ValidationError):
        MemoryExtractionResponse.model_validate(
            {
                "candidates": [
                    {
                        "text": "User wants local news.",
                        "category": "location",
                        "confidence": 0.9,
                    }
                ]
            }
        )
