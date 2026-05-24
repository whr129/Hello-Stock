from datetime import UTC, datetime

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
            lines.extend(f"   {line}" for line in _evidence_lines(item.evidence, limit=3))
        else:
            lines.append("   Evidence: not enough stored evidence yet.")
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
        lines.append("Evidence:")
        lines.extend(_evidence_lines(item.evidence, limit=3))
    else:
        lines.append("Evidence: not enough stored evidence yet.")
    if item.weak_evidence:
        lines.append("Weak or missing evidence: " + "; ".join(item.weak_evidence) + ".")
    lines.append("")
    lines.append(GUARDRAIL_TEXT)
    return "\n".join(lines)


def format_research_status(runs: list[RuntimeRun]) -> str:
    if not runs:
        return "No recent market research runs were found."
    lines = ["Recent market research and refresh runs"]
    for run in runs:
        started = run.started_at.isoformat() if run.started_at else "unknown"
        lines.append(f"- #{run.id} {run.workflow} {run.status} at {started}")
        if run.summary:
            lines.append(f"  {run.summary[:160]}")
        refresh_report = _refresh_report(run)
        if refresh_report:
            delivery = refresh_report.get("delivery_status") or "unknown"
            retry_count = refresh_report.get("retry_count", 0)
            lines.append(f"  Refresh report: delivery {delivery}, retries {retry_count}")
            warnings = _source_health_warnings(refresh_report)
            if warnings:
                lines.append(f"  Source health: {warnings}")
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


def _evidence_lines(evidence: list[dict[str, object]], *, limit: int) -> list[str]:
    lines: list[str] = []
    for item in evidence[:limit]:
        title = _clean(item.get("article_title"))
        url = _clean(item.get("article_url"))
        source = _clean(item.get("source_name")) or _clean(item.get("source_family"))
        when = _display_date(item.get("published_at") or item.get("created_at"))
        snippet = _clean(item.get("evidence_text")) or _clean(item.get("text"))

        if title and url:
            label = title
            if source:
                label += f" - {source}"
            if when:
                label += f", {when}"
            lines.append(f"Evidence: {label}: {url}")
        elif snippet:
            lines.append(f"Evidence: stored evidence, link unavailable: {snippet}")
        else:
            lines.append("Evidence: stored evidence, link unavailable.")

        if snippet and title and url:
            lines.append(f"  {snippet[:220]}")

    return lines or ["Evidence: not enough stored evidence yet."]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _display_date(value: object) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw[:10]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.date().isoformat()


def _refresh_report(run: RuntimeRun) -> dict | None:
    metadata = dict(getattr(run, "run_metadata", None) or {})
    report = metadata.get("refresh_report")
    return report if isinstance(report, dict) else None


def _source_health_warnings(report: dict) -> str:
    sources = report.get("sources")
    if not isinstance(sources, dict):
        return ""
    health = sources.get("health")
    if not isinstance(health, dict):
        return ""
    weak = [
        f"{name}={status}"
        for name, status in health.items()
        if status in {"stale", "empty", "failing", "low_signal"}
    ]
    return ", ".join(weak[:5])
