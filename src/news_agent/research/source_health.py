from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class SourceHealth:
    score: float
    status: str
    freshness_score: float
    success_score: float
    volume_score: float
    impact_score: float
    link_score: float

    def as_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "status": self.status,
            "freshness_score": self.freshness_score,
            "success_score": self.success_score,
            "volume_score": self.volume_score,
            "impact_score": self.impact_score,
            "link_score": self.link_score,
        }


def score_source_health(
    *,
    config: dict[str, Any],
    last_success_at: datetime | None,
    last_fetched_at: datetime | None,
    last_error: str | None,
    now: datetime | None = None,
) -> SourceHealth:
    now = now or datetime.now(UTC)
    metrics = dict(config.get("source_health") or {})
    fetched = _metric(metrics, "fetched")
    accepted = _metric(metrics, "accepted")
    saved = _metric(metrics, "saved")
    failed = _metric(metrics, "failed")
    link_available = _metric(metrics, "link_available")
    link_checked = _metric(metrics, "link_checked")

    freshness_score = _freshness_score(last_success_at, last_fetched_at, now)
    success_score = _ratio_score(fetched - failed, max(fetched, failed, 1))
    volume_score = min(100.0, saved * 20.0 if fetched else 75.0)
    impact_score = _ratio_score(accepted, fetched) if fetched else 75.0
    link_score = _ratio_score(link_available, link_checked) if link_checked else 75.0

    if last_error and not last_success_at:
        success_score = min(success_score, 20.0)
    score = round(
        freshness_score * 0.25
        + success_score * 0.25
        + volume_score * 0.15
        + impact_score * 0.25
        + link_score * 0.10,
        2,
    )
    return SourceHealth(
        score=score,
        status=_status(score, last_error),
        freshness_score=round(freshness_score, 2),
        success_score=round(success_score, 2),
        volume_score=round(volume_score, 2),
        impact_score=round(impact_score, 2),
        link_score=round(link_score, 2),
    )


def source_is_suppressed(
    *,
    config: dict[str, Any],
    last_success_at: datetime | None,
    last_fetched_at: datetime | None,
    last_error: str | None,
    minimum_score: float,
    now: datetime | None = None,
) -> bool:
    if bool(config.get("source_health_override")):
        return False
    health = score_source_health(
        config=config,
        last_success_at=last_success_at,
        last_fetched_at=last_fetched_at,
        last_error=last_error,
        now=now,
    )
    return health.score < minimum_score and health.status in {"failing", "low_signal", "stale"}


def update_source_health_metrics(
    config: dict[str, Any],
    *,
    fetched: int = 0,
    accepted: int = 0,
    rejected: int = 0,
    saved: int = 0,
    duplicates: int = 0,
    failed: int = 0,
    link_checked: int = 0,
    link_available: int = 0,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    updated = dict(config)
    metrics = dict(updated.get("source_health") or {})
    for key, value in {
        "fetched": fetched,
        "accepted": accepted,
        "rejected": rejected,
        "saved": saved,
        "duplicates": duplicates,
        "failed": failed,
        "link_checked": link_checked,
        "link_available": link_available,
    }.items():
        if value:
            metrics[key] = _metric(metrics, key) + int(value)
    metrics["checked_at"] = (checked_at or datetime.now(UTC)).isoformat()
    updated["source_health"] = metrics
    return updated


def _freshness_score(
    last_success_at: datetime | None,
    last_fetched_at: datetime | None,
    now: datetime,
) -> float:
    timestamp = last_success_at or last_fetched_at
    if timestamp is None:
        return 75.0
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    hours = max((now - timestamp).total_seconds() / 3600, 0.0)
    return max(0.0, min(100.0, 100.0 - hours * 2.0))


def _ratio_score(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(100.0, (numerator / denominator) * 100.0))


def _metric(metrics: dict[str, Any], key: str) -> int:
    try:
        return max(int(metrics.get(key, 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def _status(score: float, last_error: str | None) -> str:
    if last_error and score < 50:
        return "failing"
    if score < 35:
        return "low_signal"
    if score < 55:
        return "stale"
    return "healthy"
