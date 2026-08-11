from dataclasses import replace
from datetime import UTC, datetime

from news_agent.research.schemas import CandidateExplanation, EvidenceStrength
from news_agent.storage.models import MarketSignalSnapshot

HIGH_TRUST_DIRECT_SOURCE_THRESHOLD = 0.95


def explain_candidates(
    snapshots: list[MarketSignalSnapshot],
    *,
    ticker: str | None = None,
    min_strong_sources: int = 2,
) -> list[CandidateExplanation]:
    filtered = snapshots
    if ticker:
        filtered = [item for item in snapshots if item.ticker == ticker.upper()]

    explanations: list[CandidateExplanation] = []
    for index, snapshot in enumerate(filtered, start=1):
        components = dict(snapshot.component_scores or {})
        evidence = list(snapshot.evidence or [])
        profile = _evidence_profile(evidence)
        strength = _evidence_strength(snapshot, profile, min_strong_sources=min_strong_sources)
        explanations.append(
            CandidateExplanation(
                ticker=snapshot.ticker,
                theme=snapshot.theme,
                rank=index,
                total_score=snapshot.total_score,
                components={key: float(value) for key, value in components.items()},
                evidence=evidence,
                weak_evidence=_weak_evidence(snapshot, evidence),
                created_at=snapshot.created_at,
                snapshot_id=snapshot.id,
                evidence_strength=strength,
                distinct_source_count=profile["distinct_source_count"],
                linked_source_count=profile["linked_source_count"],
                max_trust_score=profile["max_trust_score"],
                suppression_reasons=_suppression_reasons(strength, profile),
            )
        )
    return explanations


def visible_candidate_explanations(
    explanations: list[CandidateExplanation],
    *,
    limit: int,
    include_developing: bool = False,
) -> list[CandidateExplanation]:
    visible = []
    seen: set[tuple[str | None, str | None]] = set()
    for item in explanations:
        key = (item.ticker, item.theme)
        allowed = item.evidence_strength == "strong" or (
            include_developing and item.evidence_strength == "developing"
        )
        if allowed and key not in seen:
            visible.append(item)
            seen.add(key)
    return [replace(item, rank=index) for index, item in enumerate(visible[:limit], start=1)]


def _weak_evidence(snapshot: MarketSignalSnapshot, evidence: list[dict[str, object]]) -> list[str]:
    weaknesses: list[str] = []
    source_families = {item.get("source_family") for item in evidence if item.get("source_family")}
    linked_sources = {
        item.get("article_url")
        for item in evidence
        if isinstance(item.get("article_url"), str) and item.get("article_url")
    }
    named_sources = {
        item.get("source_name") or item.get("source_family")
        for item in evidence
        if item.get("source_name") or item.get("source_family")
    }
    if not evidence:
        weaknesses.append("stored evidence is missing")
    if evidence and not linked_sources:
        weaknesses.append("source links are unavailable")
    if len(source_families) < 2:
        weaknesses.append("source diversity is limited")
    if len(named_sources) < 2:
        weaknesses.append("only one distinct source is currently available")
    if _is_stale(snapshot.created_at):
        weaknesses.append("signal snapshot is stale")
    if snapshot.price_momentum == 50.0:
        weaknesses.append("fresh price momentum is missing or neutral")
    if snapshot.volume_signal == 50.0:
        weaknesses.append("volume data is missing or neutral")
    if snapshot.total_score < 50.0:
        weaknesses.append("overall signal is below high-confidence threshold")
    return weaknesses or ["evidence is still a weak signal and may be noisy or stale"]


def _evidence_profile(evidence: list[dict[str, object]]) -> dict[str, object]:
    named_sources = {
        item.get("source_name") or item.get("source_family") or item.get("source_provider")
        for item in evidence
        if item.get("source_name") or item.get("source_family") or item.get("source_provider")
    }
    linked_sources = {
        item.get("article_url")
        for item in evidence
        if isinstance(item.get("article_url"), str) and item.get("article_url")
    }
    clusters = {
        item.get("evidence_cluster_id") or item.get("article_url")
        for item in evidence
        if item.get("evidence_cluster_id") or item.get("article_url")
    }
    trust_scores = [_trust_score(item) for item in evidence]
    return {
        "distinct_source_count": len(named_sources),
        "linked_source_count": len(linked_sources),
        "distinct_cluster_count": len(clusters),
        "max_trust_score": max(trust_scores, default=0.0),
        "has_direct_high_impact": any(_is_direct_high_impact(item) for item in evidence),
    }


def _evidence_strength(
    snapshot: MarketSignalSnapshot,
    profile: dict[str, object],
    *,
    min_strong_sources: int,
) -> EvidenceStrength:
    distinct_source_count = int(profile["distinct_source_count"])
    linked_source_count = int(profile["linked_source_count"])
    distinct_cluster_count = int(profile["distinct_cluster_count"])
    max_trust_score = float(profile["max_trust_score"])
    has_direct_high_impact = bool(profile["has_direct_high_impact"])
    if (
        distinct_source_count >= min_strong_sources
        and linked_source_count >= min_strong_sources
        and distinct_cluster_count >= min_strong_sources
    ):
        return "strong"
    if max_trust_score >= HIGH_TRUST_DIRECT_SOURCE_THRESHOLD and has_direct_high_impact:
        return "strong"
    if linked_source_count >= 1 and snapshot.total_score >= 50:
        return "developing"
    return "weak"


def _suppression_reasons(
    strength: EvidenceStrength,
    profile: dict[str, object],
) -> list[str]:
    if strength != "weak":
        return []
    reasons = []
    if int(profile["linked_source_count"]) < 1:
        reasons.append("no verified link-backed evidence candidate")
    if int(profile["distinct_source_count"]) < 2:
        reasons.append("not enough distinct sources for a strong candidate")
    if int(profile.get("distinct_cluster_count", 0)) < 2:
        reasons.append("not enough distinct evidence clusters for a strong candidate")
    if float(profile["max_trust_score"]) < HIGH_TRUST_DIRECT_SOURCE_THRESHOLD:
        reasons.append("no high-trust direct source")
    return reasons


def _trust_score(item: dict[str, object]) -> float:
    try:
        return float(item.get("trust_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_direct_high_impact(item: dict[str, object]) -> bool:
    source_family = str(item.get("source_family") or "").lower()
    source_provider = str(item.get("source_provider") or "").lower()
    source_name = str(item.get("source_name") or "").lower()
    title = str(item.get("article_title") or "").lower()
    direct_markers = {
        "filings",
        "filing",
        "regulatory",
        "macro",
        "company",
        "official",
        "earnings",
    }
    if source_family in direct_markers or source_provider in direct_markers:
        return True
    direct_names = ("sec", "nvidia", "federal reserve", "treasury", "bls", "bea")
    return any(marker in source_name or marker in title for marker in direct_names)


def _is_stale(created_at) -> bool:
    if created_at is None:
        return True
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - created_at).total_seconds() > 36 * 3600
