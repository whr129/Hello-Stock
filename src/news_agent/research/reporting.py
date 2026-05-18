from news_agent.research.schemas import CandidateExplanation
from news_agent.storage.models import RuntimeRun

GUARDRAIL_TEXT = (
    "Not financial advice. This is an attention and momentum research ranking; "
    "signals can be wrong, stale, or driven by noisy source concentration."
)


def format_candidates(explanations: list[CandidateExplanation]) -> str:
    if not explanations:
        return f"No market attention candidates are available yet.\n\n{GUARDRAIL_TEXT}"

    lines = ["Market attention candidates"]
    for item in explanations:
        label = item.ticker or item.theme or "Theme"
        theme = f" - {item.theme}" if item.theme and item.ticker else ""
        lines.append(f"{item.rank}. {label}{theme} - score {item.total_score:.0f}")
        lines.append(f"   Components: {_component_text(item.components)}")
        if item.evidence:
            lines.append(f"   Evidence: {_evidence_text(item.evidence)}")
        if item.weak_evidence:
            lines.append(f"   Weakness: {item.weak_evidence[0]}.")
    lines.append("")
    lines.append(GUARDRAIL_TEXT)
    return "\n".join(lines)


def format_signal(explanations: list[CandidateExplanation], ticker: str) -> str:
    if not explanations:
        return (
            f"No current signal snapshot is available for {ticker.upper()}.\n\n"
            f"{GUARDRAIL_TEXT}"
        )

    item = explanations[0]
    lines = [
        f"{ticker.upper()} signal explanation",
        f"Current rank: {item.rank or 'unranked'}",
        f"Score: {item.total_score:.0f}",
        f"Theme: {item.theme or 'none detected'}",
        f"Components: {_component_text(item.components)}",
    ]
    if item.evidence:
        lines.append(f"Evidence: {_evidence_text(item.evidence)}")
    if item.weak_evidence:
        lines.append("Weak or missing evidence: " + "; ".join(item.weak_evidence) + ".")
    lines.append("")
    lines.append(GUARDRAIL_TEXT)
    return "\n".join(lines)


def format_research_status(runs: list[RuntimeRun]) -> str:
    if not runs:
        return "No recent market research runs were found."
    lines = ["Recent market research runs"]
    for run in runs:
        started = run.started_at.isoformat() if run.started_at else "unknown"
        lines.append(f"- #{run.id} {run.workflow} {run.status} at {started}")
        if run.summary:
            lines.append(f"  {run.summary[:160]}")
    return "\n".join(lines)


def _component_text(components: dict[str, float]) -> str:
    labels = {
        "mention_velocity": "mentions",
        "source_diversity": "diversity",
        "recency_score": "recency",
        "price_momentum": "price",
        "volume_signal": "volume",
        "theme_persistence": "theme",
        "trust_score": "trust",
    }
    parts = [
        f"{label} {components[key]:.0f}"
        for key, label in labels.items()
        if key in components
    ]
    return ", ".join(parts) or "not available"


def _evidence_text(evidence: list[dict[str, object]]) -> str:
    snippets = [
        str(item.get("text", "")).strip()
        for item in evidence[:3]
        if str(item.get("text", "")).strip()
    ]
    return "; ".join(snippets) if snippets else "stored mention evidence"
