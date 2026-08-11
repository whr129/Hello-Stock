from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from news_agent.research.schemas import CandidateExplanation

LinkChecker: TypeAlias = Callable[[str], bool | Awaitable[bool]]


async def validate_candidate_links(
    explanations: list[CandidateExplanation],
    *,
    checker: LinkChecker | None = None,
    recheck_hours: int = 24,
) -> list[CandidateExplanation]:
    return await asyncio.gather(
        *[
            _validated_explanation(
                explanation,
                checker=checker,
                recheck_hours=recheck_hours,
            )
            for explanation in explanations
        ]
    )


async def _validated_explanation(
    explanation: CandidateExplanation,
    *,
    checker: LinkChecker | None,
    recheck_hours: int,
) -> CandidateExplanation:
    evidence = await asyncio.gather(
        *[
            _validated_evidence_item(item, checker=checker, recheck_hours=recheck_hours)
            for item in explanation.evidence
        ]
    )
    linked_source_count = len(
        {
            item.get("article_url")
            for item in evidence
            if item.get("article_url_status") == "available" and item.get("article_url")
        }
    )
    evidence_strength = explanation.evidence_strength
    if evidence_strength == "strong" and linked_source_count < 2:
        evidence_strength = (
            "strong" if _has_available_high_trust_direct_evidence(evidence) else "developing"
        )
    if evidence_strength == "developing" and linked_source_count < 1:
        evidence_strength = "weak"
    return replace(
        explanation,
        evidence=evidence,
        linked_source_count=linked_source_count,
        evidence_strength=evidence_strength,
    )


async def _validated_evidence_item(
    item: dict[str, object],
    *,
    checker: LinkChecker | None,
    recheck_hours: int,
) -> dict[str, object]:
    url = str(item.get("article_url") or "").strip()
    if not url:
        return {**item, "article_url_status": "missing", "link_status": "missing"}
    if _recently_checked(item, recheck_hours=recheck_hours):
        status = str(item.get("article_url_status") or item.get("link_status") or "unchecked")
        return {**item, "article_url_status": status, "link_status": status}
    available = await _check_url(url, checker=checker)
    status = "available" if available else "unavailable"
    return {
        **item,
        "article_url_status": status,
        "link_status": status,
        "link_checked_at": datetime.now(UTC).isoformat(),
    }


async def _check_url(url: str, *, checker: LinkChecker | None) -> bool:
    if checker is None:
        return await asyncio.to_thread(_default_check_url, url)
    result = checker(url)
    if hasattr(result, "__await__"):
        return bool(await result)  # type: ignore[misc]
    return bool(result)


def _default_check_url(url: str) -> bool:
    for method in ("HEAD", "GET"):
        request = Request(
            url,
            method=method,
            headers={"User-Agent": "news-agent/0.1 (market-research evidence checker)"},
        )
        try:
            with urlopen(request, timeout=2) as response:
                return int(response.status) < 400
        except HTTPError as exc:
            if method == "HEAD" and exc.code in {403, 405, 501}:
                continue
            return exc.code < 400
        except (TimeoutError, URLError, ValueError):
            return False
    return False


def _has_available_high_trust_direct_evidence(evidence: list[dict[str, object]]) -> bool:
    return any(
        item.get("article_url_status") == "available"
        and _trust_score(item) >= 0.95
        and _is_direct_high_impact(item)
        for item in evidence
    )


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


def _recently_checked(item: dict[str, object], *, recheck_hours: int) -> bool:
    status = str(item.get("article_url_status") or item.get("link_status") or "")
    if status not in {"available", "unavailable"}:
        return False
    checked_at = str(item.get("link_checked_at") or "").strip()
    if not checked_at:
        return False
    try:
        parsed = datetime.fromisoformat(checked_at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return datetime.now(UTC) - parsed <= timedelta(hours=max(recheck_hours, 0))
