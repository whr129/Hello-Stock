from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool

RoutableIntent = Literal[
    "runtime",
    "research",
    "candidates",
    "signals",
    "sourcehealth",
    "sourcepack",
    "resources",
    "general_chat",
    "help",
]
ROUTABLE_INTENTS = frozenset(get_args(RoutableIntent))

MemoryCategory = Literal["preference", "profile", "constraint", "other"]


class StrictResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RouterResponse(StrictResponseModel):
    intent: RoutableIntent
    args: list[str]


class ReflectionResponse(StrictResponseModel):
    verdict: Literal["pass", "retry", "fail"]
    reason: str = Field(max_length=500)
    corrected_intent: RoutableIntent | None
    corrected_args: list[str]


class MarketImpactResponse(StrictResponseModel):
    accepted: StrictBool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class MentionResponseItem(StrictResponseModel):
    ticker: str | None
    theme: str | None
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=500)


class MentionExtractionResponse(StrictResponseModel):
    mentions: list[MentionResponseItem] = Field(max_length=5)


ResearchSourceKind = Literal[
    "filing",
    "earnings_release",
    "company_ir",
    "regulator",
    "news",
    "market_data",
    "analysis",
]


class ResearchWebEvidenceResponse(StrictResponseModel):
    id: str
    url: str
    title: str
    published_at: datetime | None
    event_at: datetime | None
    summary: str
    source_kind: ResearchSourceKind
    form_type: str | None
    fiscal_period: str | None


class ResearchWebClaimResponse(StrictResponseModel):
    text: str
    evidence_ids: list[str] = Field(max_length=3)


class ResearchWebFinancialFactResponse(StrictResponseModel):
    metric: str
    value: str
    unit: str | None
    currency: str | None
    period_end: str
    comparison_basis: str | None
    evidence_ids: list[str] = Field(max_length=3)


class CompanyResearchResponse(StrictResponseModel):
    ticker: str
    company_name: str
    identity_status: Literal["matched", "ambiguous", "unresolved"]
    overview: str
    overview_evidence_ids: list[str] = Field(max_length=3)
    financial_period: str | None
    financial_facts: list[ResearchWebFinancialFactResponse] = Field(max_length=6)
    developments: list[ResearchWebClaimResponse] = Field(max_length=5)
    catalysts: list[ResearchWebClaimResponse] = Field(max_length=4)
    risks: list[ResearchWebClaimResponse] = Field(max_length=4)
    contradictions: list[ResearchWebClaimResponse] = Field(max_length=3)
    missing_checks: list[str] = Field(max_length=5)
    evidence: list[ResearchWebEvidenceResponse] = Field(max_length=12)
    confidence: float = Field(ge=0, le=1)


class MemoryCandidateResponse(StrictResponseModel):
    text: str = Field(min_length=1, max_length=500)
    category: MemoryCategory
    confidence: float = Field(ge=0, le=1)


class MemoryExtractionResponse(StrictResponseModel):
    candidates: list[MemoryCandidateResponse]


class MemoryConsolidationResponse(StrictResponseModel):
    action: Literal["add", "update", "skip"]
    memory_id: int | None
    text: str = Field(max_length=500)
    category: MemoryCategory
    confidence: float = Field(ge=0, le=1)


EvalTag = Literal[
    "too_generic",
    "no_evidence",
    "wrong_ticker",
    "stale_data",
    "stale_evidence",
    "hallucinated_source",
    "unclear_ranking_reason",
    "too_verbose",
    "missing_weak_evidence",
    "not_useful_research",
    "missing_links",
    "broken_link",
    "thin_evidence",
    "single_source",
    "too_many_candidates",
    "missing_safety",
]


class JudgeScores(StrictResponseModel):
    relevance: int = Field(ge=1, le=5)
    specificity: int = Field(ge=1, le=5)
    ticker_correctness: int = Field(ge=1, le=5)
    theme_correctness: int = Field(ge=1, le=5)
    evidence_quality: int = Field(ge=1, le=5)
    freshness: int = Field(ge=1, le=5)
    source_attribution: int = Field(ge=1, le=5)
    source_link_validity: int = Field(ge=1, le=5)
    grounding: int = Field(ge=1, le=5)
    explainability: int = Field(ge=1, le=5)
    usefulness: int = Field(ge=1, le=5)
    safety: int = Field(ge=1, le=5)
    concision: int = Field(ge=1, le=5)


class JudgeResponse(StrictResponseModel):
    scores: JudgeScores
    pass_: StrictBool = Field(alias="pass")
    tags: list[EvalTag]
    notes: str = Field(max_length=500)


ResponseModel = TypeVar("ResponseModel", bound=StrictResponseModel)


def strict_response_format(model: type[ResponseModel], *, name: str) -> dict[str, object]:
    schema = _openai_compatible_schema(model.model_json_schema(by_alias=True))
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def strict_responses_text_format(
    model: type[ResponseModel],
    *,
    name: str,
) -> dict[str, object]:
    chat_format = strict_response_format(model, name=name)
    schema = chat_format["json_schema"]
    assert isinstance(schema, dict)
    return {
        "format": {
            "type": "json_schema",
            "name": schema["name"],
            "strict": True,
            "schema": schema["schema"],
        }
    }


def _openai_compatible_schema(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _openai_compatible_schema(item)
            for key, item in value.items()
            if key not in {"minLength", "maxLength"}
        }
    if isinstance(value, list):
        return [_openai_compatible_schema(item) for item in value]
    return value
