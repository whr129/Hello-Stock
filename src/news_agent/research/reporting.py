from collections.abc import Mapping
from datetime import UTC, datetime

from news_agent.research.schemas import CandidateExplanation, CompanyResearchPacket, ResearchClaim
from news_agent.research.source_health import score_source_health
from news_agent.storage.models import RuntimeRun, Source

GUARDRAIL_TEXT = (
    "Not financial advice. This is an attention and momentum research ranking; "
    "signals can be wrong, stale, or driven by noisy source concentration."
)


def format_candidates(
    explanations: list[CandidateExplanation],
    *,
    max_evidence_items: int = 3,
    company_research: Mapping[str, CompanyResearchPacket] | None = None,
) -> str:
    if not explanations:
        return f"No market attention candidates are available yet.\n\n{GUARDRAIL_TEXT}"

    lines = ["Market research candidates"]
    for item in explanations:
        label = item.ticker or item.theme or "Theme"
        theme = f" - {item.theme}" if item.theme and item.ticker else ""
        lines.append(
            f"{item.rank}. {label}{theme} - score {item.total_score:.0f} "
            f"- {item.evidence_strength} evidence"
        )
        lines.append(f"   Why this ranked: {_ranking_reason(item)}")
        if item.evidence:
            lines.append("   Evidence chain:")
            lines.extend(
                f"   {line}" for line in _evidence_lines(item.evidence, limit=max_evidence_items)
            )
        else:
            lines.append("   Evidence chain: not enough stored evidence yet.")
        lines.append(f"   Score drivers: {_component_text(item.components)}")
        lines.append(
            "   Source quality: "
            f"{item.distinct_source_count} distinct sources, "
            f"{item.linked_source_count} link-backed, "
            f"max trust {item.max_trust_score:.2f}."
        )
        if item.weak_evidence:
            lines.append(f"   Weaknesses: {'; '.join(item.weak_evidence[:3])}.")
        lines.append(f"   Next checks: {_next_checks(item)}")
        packet = (company_research or {}).get((item.ticker or "").upper())
        if packet:
            lines.extend(f"   {line}" for line in _company_research_lines(packet))
    lines.append("")
    lines.append(GUARDRAIL_TEXT)
    return "\n".join(lines)


def format_signal(
    explanations: list[CandidateExplanation],
    ticker: str,
    *,
    max_evidence_items: int = 3,
    company_research: Mapping[str, CompanyResearchPacket] | None = None,
) -> str:
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
        f"Evidence strength: {item.evidence_strength}",
        f"Score movement: {_score_movement(explanations)}",
        f"Why this ranked: {_ranking_reason(item)}",
        f"Score drivers: {_component_text(item.components)}",
        (
            "Source quality: "
            f"{item.distinct_source_count} distinct sources, "
            f"{item.linked_source_count} link-backed, "
            f"max trust {item.max_trust_score:.2f}."
        ),
    ]
    if item.evidence:
        lines.append("Evidence chain:")
        lines.extend(_evidence_lines(item.evidence, limit=max_evidence_items))
    else:
        lines.append("Evidence chain: not enough stored evidence yet.")
    if item.weak_evidence:
        lines.append("Weak or missing evidence: " + "; ".join(item.weak_evidence) + ".")
    lines.append(f"Next checks: {_next_checks(item)}")
    packet = (company_research or {}).get(ticker.upper())
    if packet:
        lines.extend(_company_research_lines(packet))
    lines.append("")
    lines.append(GUARDRAIL_TEXT)
    return "\n".join(lines)


def format_source_health(sources: list[Source]) -> str:
    if not sources:
        return "No configured sources were found."
    lines = ["Research source health"]
    ranked = sorted(
        sources,
        key=lambda source: score_source_health(
            config=dict(source.config or {}),
            last_success_at=source.last_success_at,
            last_fetched_at=source.last_fetched_at,
            last_error=source.last_error,
        ).score,
    )
    for source in ranked[:12]:
        health = score_source_health(
            config=dict(source.config or {}),
            last_success_at=source.last_success_at,
            last_fetched_at=source.last_fetched_at,
            last_error=source.last_error,
        )
        metrics = dict((source.config or {}).get("source_health") or {})
        lines.append(
            f"- {source.name}: {health.status}, score {health.score:.0f}, "
            f"accepted {int(metrics.get('accepted', 0) or 0)}/"
            f"{int(metrics.get('fetched', 0) or 0)} fetched"
        )
        if source.last_error:
            lines.append(f"  Last error: {source.last_error[:140]}")
    return "\n".join(lines)


def format_research_status(runs: list[RuntimeRun], sources: list[Source] | None = None) -> str:
    if not runs:
        lines = ["No recent market research runs were found."]
    else:
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
    if sources:
        health_counts: dict[str, int] = {}
        for source in sources:
            health = score_source_health(
                config=dict(source.config or {}),
                last_success_at=source.last_success_at,
                last_fetched_at=source.last_fetched_at,
                last_error=source.last_error,
            )
            health_counts[health.status] = health_counts.get(health.status, 0) + 1
        summary = ", ".join(
            f"{status} {count}" for status, count in sorted(health_counts.items())
        )
        lines.extend(["", f"Source quality summary: {summary or 'not available'}"])
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
        "evidence_quality": "evidence",
        "novelty": "novelty",
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
        url_status = _clean(item.get("article_url_status"))
        source = _clean(item.get("source_name")) or _clean(item.get("source_family"))
        when = _display_date(item.get("published_at") or item.get("created_at"))
        snippet = _clean(item.get("evidence_text")) or _clean(item.get("text"))

        if title and url and url_status == "available":
            label = title
            if source:
                label += f" - {source}"
            if when:
                label += f", {when}"
            lines.append(f"- {label}: {url}")
            lines.append(f"  Why it matters: {_evidence_reason(item, snippet)}")
        elif title and url and url_status == "unavailable":
            label = title
            if source:
                label += f" - {source}"
            lines.append(f"- {label}: link unavailable after validation.")
            lines.append(f"  Why it matters: {_evidence_reason(item, snippet)}")
        elif title and url:
            label = title
            if source:
                label += f" - {source}"
            lines.append(f"- {label}: link not checked yet.")
            lines.append(f"  Why it matters: {_evidence_reason(item, snippet)}")
        elif snippet:
            lines.append(f"- Stored evidence, link unavailable: {snippet}")
            lines.append(f"  Why it matters: {_evidence_reason(item, snippet)}")
        else:
            lines.append("- Stored evidence, link unavailable.")

        if snippet and title:
            lines.append(f"  {snippet[:220]}")

    return lines or ["- Not enough stored evidence yet."]


def _ranking_reason(item: CandidateExplanation) -> str:
    theme = item.theme or "the detected market theme"
    label = item.ticker or theme
    if item.evidence_strength == "strong":
        return (
            f"{label} has either multiple linked sources or one high-trust direct source "
            f"supporting {theme}."
        )
    if item.evidence_strength == "developing":
        return (
            f"{label} has link-backed evidence, but source breadth or confirmation "
            "is still limited."
        )
    return f"{label} is present in stored signals, but the evidence gate is still weak."


def _evidence_reason(item: dict[str, object], snippet: str) -> str:
    stored_reason = _clean(item.get("evidence_reason"))
    if stored_reason:
        return stored_reason
    source_family = _clean(item.get("source_family")) or "source"
    trust_score = _clean(item.get("trust_score"))
    reason = f"This {source_family} item is the stored source behind the candidate thesis"
    if trust_score:
        reason += f" and carries trust {trust_score}"
    if snippet:
        reason += "; the snippet states the concrete catalyst."
    else:
        reason += "."
    return reason


def _score_movement(explanations: list[CandidateExplanation]) -> str:
    scores = [item.total_score for item in explanations[:5]]
    if len(scores) < 2:
        return "not enough history yet"
    delta = scores[0] - scores[-1]
    if abs(delta) < 1:
        return "roughly flat across recent snapshots"
    direction = "up" if delta > 0 else "down"
    return f"{direction} {abs(delta):.0f} points across recent snapshots"


def _next_checks(item: CandidateExplanation) -> str:
    checks = []
    if item.linked_source_count < 2 and item.max_trust_score < 0.95:
        checks.append("find a second independent link-backed source")
    if item.components.get("price_momentum") == 50.0:
        checks.append("confirm price follow-through")
    if item.components.get("volume_signal") == 50.0:
        checks.append("confirm volume follow-through")
    if not checks:
        checks.append("watch for follow-through in filings, earnings commentary, and market data")
    return "; ".join(checks)


def _company_research_lines(packet: CompanyResearchPacket) -> list[str]:
    lines = [
        "Live web research (supplemental; does not change the stored score):",
        f"As of: {packet.as_of.astimezone(UTC).isoformat(timespec='minutes')}",
    ]
    if packet.overview:
        lines.append(
            f"Overview: {packet.overview[:350]} {_citation_labels(packet.overview_evidence_ids)}"
        )
    if packet.financial_facts:
        lines.append(f"Latest financial period: {packet.financial_period or 'not confirmed'}")
        for fact in packet.financial_facts[:3]:
            unit = " ".join(item for item in (fact.currency, fact.unit) if item)
            comparison = f"; {fact.comparison_basis}" if fact.comparison_basis else ""
            lines.append(
                f"- {fact.metric}: {fact.value}{f' {unit}' if unit else ''} "
                f"({fact.period_end}{comparison}) {_citation_labels(fact.evidence_ids)}"
            )
    _append_claims(lines, "Recent developments", packet.developments, limit=2)
    _append_claims(lines, "Catalysts to investigate", packet.catalysts, limit=2)
    _append_claims(lines, "Risks/counterevidence", packet.risks, limit=2)
    _append_claims(lines, "Conflicting evidence", packet.contradictions, limit=2)
    if packet.missing_checks:
        lines.append("Evidence gaps: " + "; ".join(packet.missing_checks[:3]))
    lines.append(
        "Research priority: review the cited primary evidence and risks before "
        "drawing a conclusion."
    )
    lines.append(f"Web evidence quality: {packet.status}, confidence {packet.confidence:.2f}")
    lines.append("Web sources:")
    for evidence in packet.evidence[:4]:
        published = evidence.published_at or evidence.event_at
        date = published.date().isoformat() if published else "date unavailable"
        lines.append(
            f"- [{evidence.id}] {evidence.title} - {evidence.publisher}, {date}: {evidence.url}"
        )
    return lines


def _append_claims(
    lines: list[str],
    label: str,
    claims: list[ResearchClaim],
    *,
    limit: int,
) -> None:
    if not claims:
        return
    lines.append(f"{label}:")
    for claim in claims[:limit]:
        lines.append(f"- {claim.text[:350]} {_citation_labels(claim.evidence_ids)}")


def _citation_labels(evidence_ids: list[str]) -> str:
    return " ".join(f"[{evidence_id}]" for evidence_id in evidence_ids)


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
