"""Validated evidence and conservative, provider-independent event matching."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from news_agent.alerts.sentiment import SentimentObservation
from news_agent.research.web_search import canonicalize_public_url


class ArticleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    article_id: str
    revision: int = Field(default=1, ge=1)
    url: str
    title: str = Field(min_length=1)
    text: str = ""
    published_at: datetime
    first_seen_at: datetime
    tickers: list[str] = Field(default_factory=list)
    source_name: str = ""
    source_id: str = ""
    provider: str = ""
    trust_score: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    primary_source: bool = False
    category: str = ""
    content_hash: str = ""
    provider_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        canonical = canonicalize_public_url(value)
        if not canonical:
            raise ValueError("Evidence requires a public HTTP URL")
        return canonical

    @field_validator("published_at", "first_seen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evidence timestamps require a timezone")
        return value.astimezone(UTC)

    @field_validator("tickers")
    @classmethod
    def valid_tickers(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[A-Z][A-Z0-9.:-]{0,14}", value) for value in values):
            raise ValueError("Invalid evidence ticker")
        return sorted(set(values))

    @model_validator(mode="after")
    def publisher_identity(self) -> ArticleEvidence:
        # Vendor/source IDs are not independent corroborating publishers.
        host = (urlsplit(self.url).hostname or "").removeprefix("www.")
        object.__setattr__(self, "source_id", host)
        return self


def latest_evidence(evidence: Sequence[ArticleEvidence]) -> list[ArticleEvidence]:
    """Current presentation view; the caller's full revision audit is never mutated."""
    selected = []
    article_ids: set[str] = set()
    urls: set[str] = set()
    for item in sorted(
        evidence,
        key=lambda row: (row.revision, row.first_seen_at, row.content_hash, row.article_id),
        reverse=True,
    ):
        if item.article_id in article_ids or item.url in urls:
            continue
        selected.append(item)
        article_ids.add(item.article_id)
        urls.add(item.url)
    return sorted(selected, key=lambda row: (row.published_at, row.article_id))


class EventIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    scope: str
    event_type: str
    revision_key: str
    occurred_at: datetime
    tokens: list[str]
    identifiers: dict[str, str] = Field(default_factory=dict)
    url: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def event_identity(evidence: ArticleEvidence) -> EventIdentity:
    title = evidence.title.lower()
    hints = {
        "bankruptcy": r"bankrupt|chapter 11|insolven",
        "m_and_a": r"acqui[rs]|merger|buyout|takeover",
        "guidance": r"guidance|outlook|forecast",
        "earnings": r"earnings|quarterly results|quarter results",
        "regulation": r"regulator|lawsuit|antitrust|court|sec charges",
        "macro": r"inflation|interest rate|federal reserve|payroll|gdp",
    }
    event_type = next((key for key, pattern in hints.items() if re.search(pattern, title)), "other")
    tokens = sorted(
        set(re.findall(r"[a-z0-9]+", title))
        - {
            "a",
            "an",
            "the",
            "and",
            "of",
            "to",
            "in",
            "on",
            "for",
            "with",
            "as",
            "update",
            "updated",
            "correction",
            "corrected",
            "breaking",
            "says",
            "report",
        }
    )
    scope = ",".join(evidence.tickers) or " ".join(tokens[:6])
    identifiers = {
        key: str(evidence.provider_metadata[key]).strip().lower()
        for key in ("accession_number", "case_number", "counterparty", "fiscal_period")
        if isinstance(evidence.provider_metadata.get(key), (str, int))
        and str(evidence.provider_metadata[key]).strip()
    }
    identity = [scope, event_type, evidence.published_at.date().isoformat(), identifiers, tokens]
    return EventIdentity(
        key=_digest(identity),
        scope=scope,
        event_type=event_type,
        revision_key=_digest([evidence.title, evidence.text, evidence.content_hash]),
        occurred_at=evidence.published_at,
        tokens=tokens,
        identifiers=identifiers,
        url=evidence.url,
    )


def same_event(existing: EventIdentity | dict, incoming: EventIdentity | dict) -> bool:
    left, right = EventIdentity.model_validate(existing), EventIdentity.model_validate(incoming)
    if left.key == right.key:
        return True
    if left.scope != right.scope or left.event_type != right.event_type:
        return False
    if abs((left.occurred_at - right.occurred_at).total_seconds()) > 36 * 3600:
        return False
    shared = left.identifiers.keys() & right.identifiers.keys()
    if any(left.identifiers[key] != right.identifiers[key] for key in shared):
        return False
    if shared & {"accession_number", "case_number"}:
        return True
    if left.url == right.url:
        return True
    a, b = set(left.tokens), set(right.tokens)
    # Different reported figures often indicate a genuinely new development.
    if {x for x in a if x.isdigit()} != {x for x in b if x.isdigit()}:
        return False
    return len(a & b) >= 5 and len(a & b) / max(1, len(a | b)) >= 0.8


def provider_observations(evidence: ArticleEvidence) -> list[SentimentObservation]:
    metadata = evidence.provider_metadata
    provider = evidence.provider.lower().replace("-", "_")
    observations = []
    if provider in {"alpha_vantage", "alphavantage"}:
        rows, ticker_key, label_key = (
            metadata.get("ticker_sentiment", []),
            "ticker",
            "ticker_sentiment_label",
        )
        labels = {
            "bullish": "positive",
            "somewhat-bullish": "positive",
            "neutral": "neutral",
            "bearish": "negative",
            "somewhat-bearish": "negative",
        }
    elif provider == "polygon":
        rows, ticker_key, label_key = metadata.get("insights", []), "ticker", "sentiment"
        labels = {"positive": "positive", "negative": "negative", "neutral": "neutral"}
    else:
        return []
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict) or row.get(ticker_key) not in evidence.tickers:
            continue
        label = labels.get(str(row.get(label_key, "")).lower())
        if label is None:
            continue
        if provider in {"alpha_vantage", "alphavantage"}:
            try:
                score = float(row["ticker_sentiment_score"])
            except (KeyError, ValueError, TypeError):
                continue
            if not math.isfinite(score) or not -1 <= score <= 1:
                continue
            if (score > 0 and label == "negative") or (score < 0 and label == "positive"):
                continue
        observations.append(
            SentimentObservation(
                article_id=evidence.article_id,
                ticker=row[ticker_key],
                provider=provider,
                model_version="vendor-unspecified",
                label=label,
                source_id=evidence.source_id,
                published_at=evidence.published_at,
                first_seen_at=evidence.first_seen_at,
                revision=evidence.revision,
            )
        )
    return observations
