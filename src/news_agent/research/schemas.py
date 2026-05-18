from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ResearchTaskType = Literal[
    "candidate_ranking",
    "stock_lookup",
    "deep_research",
    "alert_review",
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


@dataclass(frozen=True)
class ResearchEntities:
    tickers: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchConstraints:
    max_candidates: int = 5
    minimum_confidence: float = 0.0
    source_families: list[str] = field(default_factory=list)
    include_weak_evidence: bool = True


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


@dataclass(frozen=True)
class MarketContext:
    articles: list[object] = field(default_factory=list)
    summaries: list[object] = field(default_factory=list)
    mentions: list[object] = field(default_factory=list)
    signal_snapshots: list[object] = field(default_factory=list)
    market_snapshots: list[object] = field(default_factory=list)
    theme_memories: list[object] = field(default_factory=list)
