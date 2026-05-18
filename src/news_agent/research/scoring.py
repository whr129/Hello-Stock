from datetime import UTC, datetime
from math import log1p

from news_agent.research.schemas import CandidateScore, ScoreComponents
from news_agent.settings import Settings
from news_agent.storage.models import MarketSnapshot
from news_agent.storage.repositories import MentionAggregate

WINDOW_HOURS: dict[str, int] = {"1h": 1, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}


class SignalScorer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def score(
        self,
        aggregate: MentionAggregate,
        *,
        window: str,
        market_snapshot: MarketSnapshot | None = None,
        baseline_mentions: int = 0,
        theme_memory_count: int = 0,
        now: datetime | None = None,
    ) -> CandidateScore:
        now = now or datetime.now(UTC)
        recent_mentions = aggregate.mention_count
        mention_velocity = min(
            100.0,
            (recent_mentions / max(baseline_mentions, 1)) * 25.0,
        )
        source_diversity = min(100.0, aggregate.source_count * 20.0)
        recency_score = _recency_score(aggregate.latest_seen_at, now)
        price_momentum = _price_momentum_score(market_snapshot)
        volume_signal = _volume_score(market_snapshot)
        trust_score = max(0.0, min(100.0, aggregate.average_trust * 100.0))
        theme_persistence = min(100.0, theme_memory_count * 25.0)

        components = ScoreComponents(
            mention_velocity=mention_velocity,
            source_diversity=source_diversity,
            recency_score=recency_score,
            semantic_similarity=0.0,
            price_momentum=price_momentum,
            volume_signal=volume_signal,
            theme_persistence=theme_persistence,
            trust_score=trust_score,
        )
        total_score = self.weighted_total(components)
        return CandidateScore(
            ticker=aggregate.ticker,
            theme=aggregate.theme,
            window=window,
            components=components,
            total_score=total_score,
            evidence=aggregate.evidence,
        )

    def weighted_total(self, components: ScoreComponents) -> float:
        weighted = {
            "mention_velocity": (
                components.mention_velocity,
                self.settings.signal_weight_mention_velocity,
            ),
            "source_diversity": (
                components.source_diversity,
                self.settings.signal_weight_source_diversity,
            ),
            "recency_score": (components.recency_score, self.settings.signal_weight_recency),
            "semantic_similarity": (
                components.semantic_similarity,
                self.settings.signal_weight_semantic_similarity,
            ),
            "price_momentum": (
                components.price_momentum,
                self.settings.signal_weight_price_momentum,
            ),
            "volume_signal": (components.volume_signal, self.settings.signal_weight_volume),
            "theme_persistence": (
                components.theme_persistence,
                self.settings.signal_weight_theme_persistence,
            ),
            "trust_score": (components.trust_score, self.settings.signal_weight_trust),
        }
        weight_sum = sum(weight for _, weight in weighted.values())
        if weight_sum <= 0:
            return 0.0
        return round(sum(value * weight for value, weight in weighted.values()) / weight_sum, 2)


def _recency_score(seen_at: datetime | None, now: datetime) -> float:
    if seen_at is None:
        return 0.0
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=UTC)
    hours = max((now - seen_at).total_seconds() / 3600, 0.0)
    return round(max(0.0, 100.0 - hours * 4.0), 2)


def _price_momentum_score(snapshot: MarketSnapshot | None) -> float:
    if snapshot is None or snapshot.percent_change is None:
        return 50.0
    return round(max(0.0, min(100.0, 50.0 + snapshot.percent_change * 5.0)), 2)


def _volume_score(snapshot: MarketSnapshot | None) -> float:
    if snapshot is None:
        return 50.0
    indicators = snapshot.indicators or {}
    for key in ("relative_volume", "volume_ratio", "volume_vs_average"):
        value = indicators.get(key)
        if isinstance(value, int | float):
            return round(max(0.0, min(100.0, 50.0 + log1p(max(value - 1, 0.0)) * 25.0)), 2)
    if indicators.get("abnormal_volume"):
        return 75.0
    return 50.0
