from datetime import UTC, datetime, timedelta

from news_agent.research.analysis import explain_candidates, visible_candidate_explanations
from news_agent.research.scoring import SignalScorer
from news_agent.settings import Settings
from news_agent.storage.models import (
    Article,
    MarketMention,
    MarketSignalSnapshot,
    MarketSnapshot,
    Source,
)
from news_agent.storage.repositories import (
    MentionAggregate,
    _mention_evidence_payload,
    _select_preferred_signal_snapshots,
)


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


def test_score_includes_evidence_quality_and_novelty() -> None:
    scorer = SignalScorer(Settings(openai_api_key=""))
    aggregate = MentionAggregate(
        "NVDA",
        "AI infrastructure",
        4,
        2,
        0.9,
        datetime.now(UTC),
        [
            {
                "article_url": "https://example.com/nvda",
                "source_name": "Nvidia",
                "trust_score": 0.98,
                "source_health_score": 90,
                "evidence_cluster_id": "company:nvda",
            },
            {
                "article_url": "https://example.com/capex",
                "source_name": "Cloud Buyer",
                "trust_score": 0.9,
                "source_health_score": 80,
                "evidence_cluster_id": "filing:capex",
            },
        ],
    )

    score = scorer.score(aggregate, window="24h", theme_memory_count=0)

    assert score.components.evidence_quality > 70
    assert score.components.novelty > 80


def test_candidate_explanation_flags_missing_links_and_stale_single_source() -> None:
    snapshot = MarketSignalSnapshot(
        ticker="MU",
        theme="memory chips",
        window="24h",
        total_score=60,
        component_scores={"mention_velocity": 70},
        evidence=[{"text": "HBM demand coverage accelerated.", "source_family": "news"}],
        created_at=datetime.now(UTC) - timedelta(days=3),
    )

    explanation = explain_candidates([snapshot])[0]

    assert "source links are unavailable" in explanation.weak_evidence
    assert "only one distinct source is currently available" in explanation.weak_evidence
    assert "signal snapshot is stale" in explanation.weak_evidence
    assert explanation.evidence_strength == "weak"
    assert "not enough distinct sources for a strong candidate" in explanation.suppression_reasons


def test_candidate_explanation_marks_multiple_linked_sources_strong() -> None:
    snapshot = MarketSignalSnapshot(
        ticker="NVDA",
        theme="AI infrastructure",
        window="24h",
        total_score=82,
        component_scores={"mention_velocity": 86},
        evidence=[
            {
                "article_title": "Nvidia demand accelerates",
                "article_url": "https://example.com/nvidia",
                "source_name": "Nvidia",
                "source_family": "company",
                "trust_score": 0.96,
            },
            {
                "article_title": "Cloud capex expands",
                "article_url": "https://example.com/capex",
                "source_name": "Hyperscaler",
                "source_family": "filings",
                "trust_score": 0.95,
            },
        ],
        created_at=datetime.now(UTC),
    )

    explanation = explain_candidates([snapshot])[0]

    assert explanation.evidence_strength == "strong"
    assert explanation.distinct_source_count == 2
    assert explanation.linked_source_count == 2
    assert explanation.max_trust_score == 0.96


def test_candidate_explanation_marks_high_trust_direct_source_strong() -> None:
    snapshot = MarketSignalSnapshot(
        ticker="NVDA",
        theme="AI infrastructure",
        window="24h",
        total_score=80,
        evidence=[
            {
                "article_title": "Nvidia earnings release",
                "article_url": "https://example.com/nvidia-earnings",
                "source_name": "Nvidia",
                "source_family": "company",
                "trust_score": 0.98,
            }
        ],
        created_at=datetime.now(UTC),
    )

    explanation = explain_candidates([snapshot])[0]

    assert explanation.evidence_strength == "strong"


def test_visible_candidates_default_to_strong_evidence_only() -> None:
    strong = MarketSignalSnapshot(
        ticker="NVDA",
        theme="AI infrastructure",
        window="24h",
        total_score=80,
        evidence=[
            {
                "article_url": "https://example.com/nvda",
                "source_name": "Nvidia",
                "source_family": "company",
                "trust_score": 0.98,
            }
        ],
        created_at=datetime.now(UTC),
    )
    developing = MarketSignalSnapshot(
        ticker="MU",
        theme="memory chips",
        window="24h",
        total_score=70,
        evidence=[
            {
                "article_url": "https://example.com/hbm",
                "source_name": "Example Markets",
                "trust_score": 0.8,
            }
        ],
        created_at=datetime.now(UTC),
    )

    visible = visible_candidate_explanations(explain_candidates([developing, strong]), limit=3)

    assert [item.ticker for item in visible] == ["NVDA"]


def test_visible_candidates_deduplicate_same_ticker_theme_signal() -> None:
    first = MarketSignalSnapshot(
        ticker=None,
        theme="AI",
        window="24h",
        total_score=80,
        evidence=[
            {"article_url": "https://example.com/1", "source_name": "Source A"},
            {"article_url": "https://example.com/2", "source_name": "Source B"},
        ],
        created_at=datetime.now(UTC),
    )
    duplicate = MarketSignalSnapshot(
        ticker=None,
        theme="AI",
        window="7d",
        total_score=70,
        evidence=[
            {"article_url": "https://example.com/1", "source_name": "Source A"},
            {"article_url": "https://example.com/2", "source_name": "Source B"},
        ],
        created_at=datetime.now(UTC),
    )

    visible = visible_candidate_explanations(explain_candidates([first, duplicate]), limit=3)

    assert len(visible) == 1
    assert visible[0].rank == 1
    assert visible[0].theme == "AI"


def test_duplicate_evidence_clusters_do_not_make_candidate_strong() -> None:
    snapshot = MarketSignalSnapshot(
        ticker="NVDA",
        theme="AI infrastructure",
        window="24h",
        total_score=82,
        evidence=[
            {
                "article_title": "Nvidia demand accelerates",
                "article_url": "https://example.com/nvidia-a",
                "source_name": "Source A",
                "trust_score": 0.8,
                "evidence_cluster_id": "wire:nvidia demand accelerates",
            },
            {
                "article_title": "Nvidia demand accelerates",
                "article_url": "https://example.com/nvidia-b",
                "source_name": "Source B",
                "trust_score": 0.8,
                "evidence_cluster_id": "wire:nvidia demand accelerates",
            },
        ],
        created_at=datetime.now(UTC),
    )

    explanation = explain_candidates([snapshot])[0]

    assert explanation.evidence_strength == "developing"


def test_mention_evidence_payload_includes_article_and_source_links() -> None:
    published_at = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    mention = MarketMention(
        id=7,
        ticker="MU",
        theme="memory chips",
        article_id=3,
        source_id=2,
        evidence_text="HBM demand coverage accelerated.",
        source_family="news",
        trust_score=0.8,
        created_at=published_at,
    )
    article = Article(
        id=3,
        source_id=2,
        url="https://example.com/hbm",
        title="HBM demand accelerates",
        content_hash="abc",
        published_at=published_at,
    )
    source = Source(
        id=2,
        name="Example Markets",
        url="https://example.com/feed.xml",
        provider="rss",
        external_account="https://example.com/feed.xml",
    )

    payload = _mention_evidence_payload(mention, article, source)

    assert payload["article_title"] == "HBM demand accelerates"
    assert payload["article_url"] == "https://example.com/hbm"
    assert payload["source_name"] == "Example Markets"
    assert payload["source_provider"] == "rss"
    assert payload["evidence_text"] == "HBM demand coverage accelerated."


def test_preferred_signal_snapshots_choose_linked_latest_distinct_signals() -> None:
    older_unlinked = MarketSignalSnapshot(
        ticker="MU",
        theme="memory chips",
        window="24h",
        total_score=90,
        evidence=[{"article_id": 1, "text": "old"}],
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
    )
    newer_linked = MarketSignalSnapshot(
        ticker="MU",
        theme="memory chips",
        window="24h",
        total_score=40,
        evidence=[{"article_url": "https://example.com/hbm", "text": "new"}],
        created_at=datetime(2026, 5, 23, tzinfo=UTC),
    )
    other_signal = MarketSignalSnapshot(
        ticker="NVDA",
        theme="AI infrastructure",
        window="24h",
        total_score=50,
        evidence=[{"article_url": "https://example.com/ai", "text": "ai"}],
        created_at=datetime(2026, 5, 22, tzinfo=UTC),
    )

    selected = _select_preferred_signal_snapshots(
        [older_unlinked, newer_linked, other_signal],
        limit=5,
    )

    assert selected == [newer_linked, other_signal]
