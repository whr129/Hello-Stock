from news_agent.research.schemas import CandidateExplanation
from news_agent.storage.models import MarketSignalSnapshot


def explain_candidates(
    snapshots: list[MarketSignalSnapshot],
    *,
    ticker: str | None = None,
) -> list[CandidateExplanation]:
    filtered = snapshots
    if ticker:
        filtered = [item for item in snapshots if item.ticker == ticker.upper()]

    explanations: list[CandidateExplanation] = []
    for index, snapshot in enumerate(filtered, start=1):
        components = dict(snapshot.component_scores or {})
        evidence = list(snapshot.evidence or [])
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
            )
        )
    return explanations


def _weak_evidence(snapshot: MarketSignalSnapshot, evidence: list[dict[str, object]]) -> list[str]:
    weaknesses: list[str] = []
    source_families = {item.get("source_family") for item in evidence if item.get("source_family")}
    if len(source_families) < 2:
        weaknesses.append("source diversity is limited")
    if snapshot.price_momentum == 50.0:
        weaknesses.append("fresh price momentum is missing or neutral")
    if snapshot.volume_signal == 50.0:
        weaknesses.append("volume data is missing or neutral")
    if snapshot.total_score < 50.0:
        weaknesses.append("overall signal is below high-confidence threshold")
    return weaknesses or ["evidence is still a weak signal and may be noisy or stale"]
