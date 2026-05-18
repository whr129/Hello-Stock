from datetime import UTC, datetime

from news_agent.research.scoring import SignalScorer
from news_agent.settings import Settings
from news_agent.storage.models import MarketSnapshot
from news_agent.storage.repositories import MentionAggregate


def test_weighted_total_sorts_stronger_mentions_higher() -> None:
    scorer = SignalScorer(Settings(openai_api_key=""))
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    weak = MentionAggregate("MU", "memory chips", 1, 1, 0.5, now, [])
    strong = MentionAggregate("NVDA", "AI infrastructure", 6, 3, 0.8, now, [])

    weak_score = scorer.score(weak, window="24h", now=now)
    strong_score = scorer.score(strong, window="24h", now=now)

    assert strong_score.total_score > weak_score.total_score


def test_missing_price_and_volume_data_stays_neutral() -> None:
    scorer = SignalScorer(Settings(openai_api_key=""))
    aggregate = MentionAggregate("MU", None, 2, 1, 0.5, datetime.now(UTC), [])

    score = scorer.score(aggregate, window="24h")

    assert score.components.price_momentum == 50.0
    assert score.components.volume_signal == 50.0


def test_price_and_volume_components_use_snapshot_indicators() -> None:
    scorer = SignalScorer(Settings(openai_api_key=""))
    aggregate = MentionAggregate("MU", None, 2, 1, 0.5, datetime.now(UTC), [])
    snapshot = MarketSnapshot(
        symbol="MU",
        price=100,
        percent_change=4,
        indicators={"relative_volume": 3},
    )

    score = scorer.score(aggregate, window="24h", market_snapshot=snapshot)

    assert score.components.price_momentum > 50
    assert score.components.volume_signal > 50
