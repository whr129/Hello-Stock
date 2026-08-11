import json

import pytest
from pydantic import ValidationError

from news_agent.llm_contracts import (
    MarketImpactResponse,
    MemoryExtractionResponse,
    MentionExtractionResponse,
    RouterResponse,
    strict_response_format,
    strict_responses_text_format,
)


def test_strict_response_format_uses_json_schema() -> None:
    response_format = strict_response_format(RouterResponse, name="route")

    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert "maxLength" not in json.dumps(schema["schema"])


def test_strict_responses_text_format_uses_responses_shape() -> None:
    response_format = strict_responses_text_format(RouterResponse, name="route")

    assert response_format["format"]["type"] == "json_schema"
    assert response_format["format"]["strict"] is True
    assert response_format["format"]["schema"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "unsupported", "args": []},
        {"intent": "research", "args": [], "extra": True},
        {"intent": "research", "args": "NVDA"},
    ],
)
def test_router_response_rejects_invalid_contract(payload: dict) -> None:
    with pytest.raises(ValidationError):
        RouterResponse.model_validate_json(json.dumps(payload))


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
