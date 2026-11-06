from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from news_agent.alerts.decision import SentimentLabel


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SentimentObservation(_Record):
    article_id: str
    ticker: str
    provider: str
    model_version: str
    label: SentimentLabel
    source_id: str
    published_at: datetime
    first_seen_at: datetime
    event_id: str | None = None
    revision: int = Field(default=1, ge=1)
    alert_eligible: bool = True

    @field_validator("published_at", "first_seen_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class SentimentWindow(_Record):
    start: datetime
    end: datetime
    observed_count: int
    story_count: int
    source_count: int
    counts: dict[str, int]
    shares: dict[str, float]
    mean: float | None
    disagreement: float | None


class SentimentWindows(_Record):
    ticker: str
    provider: str
    model_version: str
    as_of: datetime
    current: SentimentWindow
    prior: SentimentWindow
    watermark: str
    latest_eligible_at: datetime | None

    @property
    def series_key(self) -> str:
        duration = int((self.current.end - self.current.start).total_seconds())
        return f"{self.provider}:{self.model_version}:{self.ticker}:{duration}"


class SentimentChangeState(_Record):
    series_key: str
    watermark: str = ""
    last_observation_at: datetime | None = None
    direction: Literal[-1, 0, 1] = 0
    consecutive_batches: int = Field(default=0, ge=0)
    last_alert_at: datetime | None = None

    @field_validator("last_observation_at", "last_alert_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value else None


class SentimentChange(_Record):
    eligible: bool
    reason: str
    delta: float | None
    state: SentimentChangeState


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Sentiment timestamps must include a timezone")
    return value.astimezone(UTC)


def aggregate_windows(
    observations: Iterable[SentimentObservation],
    *,
    ticker: str,
    provider: str,
    model_version: str,
    as_of: datetime,
    window: timedelta = timedelta(hours=24),
    max_lateness: timedelta = timedelta(hours=2),
) -> SentimentWindows:
    """Aggregate [start, end) publication windows using only evidence available at as_of."""
    as_of = _utc(as_of)
    if window <= timedelta(0) or max_lateness < timedelta(0):
        raise ValueError("Invalid sentiment window limits")
    current_start = as_of - window
    prior_start = current_start - window
    # Revision availability, rather than a future corrected value, defines historical evidence.
    latest: dict[str, SentimentObservation] = {}
    for item in observations:
        if (item.ticker, item.provider, item.model_version) != (ticker, provider, model_version):
            continue
        if item.first_seen_at > as_of or item.published_at >= as_of:
            continue
        previous = latest.get(item.article_id)
        if previous is None or (item.revision, item.first_seen_at) > (
            previous.revision,
            previous.first_seen_at,
        ):
            latest[item.article_id] = item
        elif (item.revision, item.first_seen_at) == (previous.revision, previous.first_seen_at):
            if item != previous:
                # Conflicting same-revision evidence cannot establish a fresh sentiment change.
                base = min((item, previous), key=lambda record: record.model_dump_json())
                latest[item.article_id] = base.model_copy(
                    update={"label": "unknown", "alert_eligible": False}
                )

    groups: dict[tuple[str, str], list[SentimentObservation]] = {}
    for item in latest.values():
        key = ("event", item.event_id) if item.event_id else ("article", item.article_id)
        groups.setdefault(key, []).append(item)
    current_groups = []
    prior_groups = []
    eligible_times = []
    included = []
    for items in groups.values():
        published_at = min(item.published_at for item in items)
        if published_at < prior_start:
            continue
        included.extend(items)
        if published_at >= current_start:
            current_groups.append(items)
            eligible_times.extend(
                item.first_seen_at
                for item in items
                if item.alert_eligible
                and item.revision == 1
                and item.label != "unknown"
                and timedelta(0) <= item.first_seen_at - item.published_at <= max_lateness
            )
        else:
            prior_groups.append(items)
    fingerprints = sorted(
        (
            item.article_id,
            item.revision,
            item.label,
            item.event_id or "",
            item.source_id,
            item.published_at.isoformat(),
            item.first_seen_at.isoformat(),
            item.alert_eligible,
        )
        for item in included
    )
    watermark = hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest()
    return SentimentWindows(
        ticker=ticker,
        provider=provider,
        model_version=model_version,
        as_of=as_of,
        current=_summarize(current_groups, current_start, as_of),
        prior=_summarize(prior_groups, prior_start, current_start),
        watermark=watermark,
        latest_eligible_at=max(eligible_times, default=None),
    )


def _summarize(
    groups: list[list[SentimentObservation]], start: datetime, end: datetime
) -> SentimentWindow:
    counts: Counter[str] = Counter()
    sources = set()
    for items in groups:
        labels = {item.label for item in items} - {"unknown"}
        if "mixed" in labels or {"positive", "negative"}.issubset(labels):
            label = "mixed"
        elif "positive" in labels:
            label = "positive"
        elif "negative" in labels:
            label = "negative"
        elif "neutral" in labels:
            label = "neutral"
        else:
            label = "unknown"
        counts[label] += 1
        if label != "unknown":
            sources.update(item.source_id for item in items if item.label != "unknown")
    known = sum(counts.values()) - counts["unknown"]
    labels = ("positive", "negative", "neutral", "mixed")
    return SentimentWindow(
        start=start,
        end=end,
        observed_count=len(groups),
        story_count=known,
        source_count=len(sources),
        counts={label: counts[label] for label in (*labels, "unknown")},
        shares={label: counts[label] / known if known else 0.0 for label in labels},
        mean=(counts["positive"] - counts["negative"]) / known if known else None,
        disagreement=(2 * min(counts["positive"], counts["negative"]) + counts["mixed"]) / known
        if known
        else None,
    )


def evaluate_change(
    windows: SentimentWindows,
    *,
    previous: SentimentChangeState | None = None,
    minimum_stories: int = 3,
    minimum_sources: int = 2,
    change_threshold: float = 0.5,
    required_batches: int = 2,
    cooldown: timedelta = timedelta(hours=2),
    max_staleness: timedelta = timedelta(hours=2),
    provider_healthy: bool = True,
) -> SentimentChange:
    """Return a candidate for Jev review; delivery owns updating state.last_alert_at."""
    if (
        minimum_stories < 1
        or minimum_sources < 1
        or required_batches < 1
        or not 0 < change_threshold <= 2
        or cooldown < timedelta(0)
        or max_staleness <= timedelta(0)
    ):
        raise ValueError("Invalid sentiment change limits")
    same_series = previous is not None and previous.series_key == windows.series_key
    previous = previous if same_series else None
    last_alert_at = previous.last_alert_at if previous else None
    state = SentimentChangeState(
        series_key=windows.series_key,
        watermark=windows.watermark,
        last_observation_at=windows.latest_eligible_at,
        last_alert_at=last_alert_at,
    )
    delta = None
    if windows.current.mean is not None and windows.prior.mean is not None:
        delta = windows.current.mean - windows.prior.mean
    reason = None
    if not provider_healthy:
        reason = "provider_unavailable"
    elif any(
        w.story_count < minimum_stories or w.source_count < minimum_sources
        for w in (windows.current, windows.prior)
    ):
        reason = "insufficient_coverage"
    elif windows.latest_eligible_at is None:
        reason = "no_fresh_evidence"
    elif windows.as_of - windows.latest_eligible_at > max_staleness:
        reason = "stale_evidence"
    elif delta is None or abs(delta) < change_threshold:
        reason = "below_threshold"
    if reason:
        return SentimentChange(eligible=False, reason=reason, delta=delta, state=state)

    new_batch = previous is None or (
        windows.watermark != previous.watermark
        and windows.latest_eligible_at is not None
        and (
            previous.last_observation_at is None
            or windows.latest_eligible_at > previous.last_observation_at
        )
    )
    direction = 1 if delta is not None and delta > 0 else -1
    prior_count = (
        previous.consecutive_batches if previous and previous.direction == direction else 0
    )
    count = prior_count + int(new_batch)
    state = state.model_copy(update={"direction": direction, "consecutive_batches": count})
    if not new_batch:
        reason = "no_new_batch"
    elif count < required_batches:
        reason = "awaiting_confirmation"
    elif last_alert_at is not None and windows.as_of - last_alert_at < cooldown:
        reason = "cooldown"
    else:
        reason = "sustained_change"
    return SentimentChange(
        eligible=reason == "sustained_change", reason=reason, delta=delta, state=state
    )
