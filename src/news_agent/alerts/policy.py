"""Host delivery limits: these never promote a Jev routing decision."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from news_agent.alerts.decision import JevDecision
from news_agent.alerts.evidence import ArticleEvidence, latest_evidence
from news_agent.alerts.sentiment import SentimentWindows


class DeliveryPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    shadow_mode: bool = True
    chat_id: int = 0
    min_confidence: float = Field(default=0.70, ge=0, le=1)
    max_age_minutes: int = Field(default=30, ge=1)
    cooldown_minutes: int = Field(default=60, ge=0)
    max_per_hour: int = Field(default=5, ge=1)
    quiet_start: str = ""
    quiet_end: str = ""
    timezone: str = "America/Toronto"
    critical_override: bool = True

    @model_validator(mode="after")
    def valid_quiet_window(self) -> DeliveryPolicyConfig:
        ZoneInfo(self.timezone)
        if bool(self.quiet_start) != bool(self.quiet_end):
            raise ValueError("Quiet hours require both start and end")
        if self.quiet_start and self.quiet_start == self.quiet_end:
            raise ValueError("Quiet hours must have different start and end times")
        for value in (self.quiet_start, self.quiet_end):
            if value:
                time.fromisoformat(value)
        return self

    @classmethod
    def from_settings(cls, settings: Any) -> DeliveryPolicyConfig:
        return cls(
            **{
                name: getattr(
                    settings,
                    "market_alerts_enabled" if name == "enabled" else f"market_alert_{name}",
                )
                for name in cls.model_fields
            }
        )


@dataclass(frozen=True)
class DeliveryGuard:
    allowed: bool
    reason: str
    not_before: datetime | None = None


def is_material_update(
    decision: JevDecision,
    *,
    event_revision: int,
    delivery_history: Sequence[Mapping[str, Any]],
) -> bool:
    """A trusted content revision plus Jev's material-new-facts judgment, never a keyword."""
    if decision.delivery != "immediate_alert" or decision.reason_code != "material_new_event":
        return False
    delivered = [entry for entry in delivery_history if entry.get("status") == "delivered"]
    accepted = [
        entry
        for entry in delivery_history
        if entry.get("status") in {"delivered", "pending", "sending", "unknown", "failed"}
    ]
    if not delivered or any(type(entry.get("revision")) is not int for entry in accepted):
        return False
    return event_revision > max(entry["revision"] for entry in accepted)


def guard_delivery(
    decision: JevDecision,
    evidence: Sequence[ArticleEvidence],
    *,
    config: DeliveryPolicyConfig,
    now: datetime,
    last_delivered_at: datetime | None = None,
    recent_deliveries: Sequence[datetime] = (),
    material_update: bool = False,
) -> DeliveryGuard:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Delivery time requires a timezone")
    if decision.delivery != "immediate_alert":
        return DeliveryGuard(False, decision.delivery)
    if not config.enabled:
        return DeliveryGuard(False, "disabled")
    if config.shadow_mode:
        return DeliveryGuard(False, "shadow_mode")
    if not config.chat_id:
        return DeliveryGuard(False, "missing_recipient")
    if decision.confidence < config.min_confidence:
        return DeliveryGuard(False, "low_confidence")
    fresh = [
        item
        for item in evidence
        if (
            timedelta(0) <= now - item.published_at <= timedelta(minutes=config.max_age_minutes)
            and item.first_seen_at <= now
        )
    ]
    if not fresh:
        return DeliveryGuard(False, "stale_evidence")
    trusted = [item for item in fresh if item.trust_score >= 0.7]
    if (
        not any(item.primary_source for item in trusted)
        and len({item.source_id for item in trusted}) < 2
    ):
        return DeliveryGuard(False, "insufficient_corroboration")
    deadlines: list[tuple[datetime, str]] = []
    if last_delivered_at is not None and not (
        material_update and decision.reason_code == "material_new_event"
    ):
        deadline = last_delivered_at + timedelta(minutes=config.cooldown_minutes)
        if deadline > now:
            deadlines.append((deadline, "cooldown"))
    recent = sorted(value for value in recent_deliveries if now - timedelta(hours=1) < value <= now)
    if len(recent) >= config.max_per_hour:
        deadlines.append((recent[-config.max_per_hour] + timedelta(hours=1), "hourly_cap"))
    critical = (
        config.critical_override and decision.materiality >= 2.9 and decision.confidence >= 0.95
    )
    if config.quiet_start and not critical:
        zone = ZoneInfo(config.timezone)
        local = now.astimezone(zone)
        start, end = time.fromisoformat(config.quiet_start), time.fromisoformat(config.quiet_end)
        clock = local.time().replace(tzinfo=None)
        quiet = start <= clock < end if start < end else clock >= start or clock < end
        if quiet:
            # Advance on the UTC timeline so nonexistent/repeated DST hours are handled.
            deadline = now.astimezone(UTC).replace(second=0, microsecond=0)
            for _ in range(26 * 60):
                deadline += timedelta(minutes=1)
                candidate = deadline.astimezone(zone).time().replace(tzinfo=None)
                active = (
                    start <= candidate < end
                    if start < end
                    else candidate >= start or candidate < end
                )
                if not active:
                    break
            deadlines.append((deadline, "quiet_hours"))
    if deadlines:
        deadline, reason = max(deadlines)
        return DeliveryGuard(False, reason, deadline)
    return DeliveryGuard(True, "allowed")


_REASONS = {
    "material_new_event": "Material new event",
    "sentiment_shift": "Change in news sentiment",
    "routine_update": "Routine update",
    "insufficient_evidence": "Insufficient evidence",
    "stale_or_duplicate": "Stale or duplicate report",
}


def format_alert(
    decision: JevDecision,
    evidence: Sequence[ArticleEvidence],
    *,
    kind: str = "shock",
    windows: SentimentWindows | None = None,
) -> str:
    heading = "News sentiment alert" if kind == "sentiment" else "Market news alert"
    lines = [heading, _REASONS[decision.reason_code]]
    for item in latest_evidence(evidence):
        lines.extend(["", item.title[:500], item.published_at.isoformat(), item.url])
    if windows is not None:
        lines.extend(
            [
                "",
                f"{windows.ticker}: {windows.current.story_count} current stories; "
                f"{windows.prior.story_count} prior stories.",
                f"News tone: {windows.current.mean}; prior: {windows.prior.mean}.",
            ]
        )
    lines.extend(["", "News evidence only; not investment advice. Sources may be incomplete."])
    return "\n".join(lines)
