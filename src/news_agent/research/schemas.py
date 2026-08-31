from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ResearchTaskType = Literal[
    "candidate_ranking",
    "stock_lookup",
    "deep_research",
    "alert_review",
    "source_health",
    "source_admin",
]
ResearchHorizon = Literal["intraday", "7d", "30d"]
ResearchAgentName = Literal[
    "news",
    "market",
    "macro",
    "social",
    "filings",
    "memory",
    "analysis",
    "report",
]
ResearchOutputFormat = Literal["telegram_summary", "long_report", "alert", "pdf_later"]
EvidenceStrength = Literal["strong", "developing", "weak"]
CompanyResearchStatus = Literal["complete", "partial", "failed", "unavailable", "timeout"]
CompanyIdentityStatus = Literal["matched", "ambiguous", "unresolved"]


@dataclass(frozen=True)
class ResearchEntities:
    tickers: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchConstraints:
    max_candidates: int = 3
    minimum_confidence: float = 0.0
    source_families: list[str] = field(default_factory=list)
    include_weak_evidence: bool = True
    include_developing_evidence: bool = False


@dataclass(frozen=True)
class ResearchPlan:
    task_type: ResearchTaskType
    entities: ResearchEntities
    research_horizon: ResearchHorizon
    agents_to_run: list[ResearchAgentName]
    output_format: ResearchOutputFormat
    constraints: ResearchConstraints = field(default_factory=ResearchConstraints)
    command: str = ""
    query: str = ""


@dataclass(frozen=True)
class ExtractedMention:
    ticker: str | None
    theme: str | None
    mention_count: int
    evidence_text: str
    source_family: str = "news"
    trust_score: float = 0.5
    article_id: int | None = None
    summary_id: int | None = None
    source_id: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ScoreComponents:
    mention_velocity: float = 0.0
    source_diversity: float = 0.0
    recency_score: float = 0.0
    semantic_similarity: float = 0.0
    price_momentum: float = 0.0
    volume_signal: float = 0.0
    theme_persistence: float = 0.0
    trust_score: float = 0.0
    evidence_quality: float = 0.0
    novelty: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "mention_velocity": self.mention_velocity,
            "source_diversity": self.source_diversity,
            "recency_score": self.recency_score,
            "semantic_similarity": self.semantic_similarity,
            "price_momentum": self.price_momentum,
            "volume_signal": self.volume_signal,
            "theme_persistence": self.theme_persistence,
            "trust_score": self.trust_score,
            "evidence_quality": self.evidence_quality,
            "novelty": self.novelty,
        }


@dataclass(frozen=True)
class CandidateScore:
    ticker: str | None
    theme: str | None
    window: str
    components: ScoreComponents
    total_score: float
    evidence: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateExplanation:
    ticker: str | None
    theme: str | None
    rank: int | None
    total_score: float
    components: dict[str, float]
    evidence: list[dict[str, object]]
    weak_evidence: list[str]
    created_at: datetime | None = None
    snapshot_id: int | None = None
    evidence_strength: EvidenceStrength = "weak"
    distinct_source_count: int = 0
    linked_source_count: int = 0
    max_trust_score: float = 0.0
    suppression_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchWebEvidence:
    id: str
    ticker: str
    title: str
    publisher: str
    url: str
    canonical_url: str
    source_kind: str
    source_tier: int
    trust_score: float
    primary: bool
    published_at: datetime | None
    event_at: datetime | None
    retrieved_at: datetime
    summary: str
    form_type: str | None = None
    fiscal_period: str | None = None
    claim_ids: list[str] = field(default_factory=list)
    cluster_id: str = ""
    link_status: Literal["available", "unavailable"] = "available"
    origin: Literal["live_web"] = "live_web"


@dataclass(frozen=True)
class ResearchClaim:
    text: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchFinancialFact:
    metric: str
    value: str
    unit: str | None
    currency: str | None
    period_end: str
    comparison_basis: str | None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompanyResearchPacket:
    ticker: str
    company_name: str
    identity_status: CompanyIdentityStatus
    as_of: datetime
    status: CompanyResearchStatus
    overview: str = ""
    overview_evidence_ids: list[str] = field(default_factory=list)
    financial_period: str | None = None
    financial_facts: list[ResearchFinancialFact] = field(default_factory=list)
    developments: list[ResearchClaim] = field(default_factory=list)
    catalysts: list[ResearchClaim] = field(default_factory=list)
    risks: list[ResearchClaim] = field(default_factory=list)
    contradictions: list[ResearchClaim] = field(default_factory=list)
    missing_checks: list[str] = field(default_factory=list)
    evidence: list[ResearchWebEvidence] = field(default_factory=list)
    confidence: float = 0.0
    errors: list[str] = field(default_factory=list)
