from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from news_agent.research.extraction import MentionExtractor
from news_agent.research.scoring import SignalScorer
from news_agent.settings import Settings
from news_agent.storage.models import MarketMention
from news_agent.storage.repositories import (
    MarketMentionRepository,
    MarketRepository,
    MarketSignalRepository,
    MarketThemeMemoryRepository,
)

SCORING_WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


async def extract_market_mentions(session: AsyncSession, *, limit: int = 50) -> int:
    repository = MarketMentionRepository(session)
    extractor = MentionExtractor()
    saved = 0

    for article, source in await repository.list_articles_for_extraction(limit=limit):
        source_family = article.category or "news"
        trust_score = source.trust_score if source else 0.5
        text = " ".join(part for part in (article.title, article.extracted_text or "") if part)
        for mention in extractor.extract(
            text=text,
            related_tickers=article.related_tickers,
            source_family=source_family,
            trust_score=trust_score,
            article_id=article.id,
            source_id=article.source_id,
        ):
            await repository.save(_to_model(mention))
            saved += 1

    for summary, article, source in await repository.list_summaries_for_extraction(limit=limit):
        related_tickers = article.related_tickers if article else []
        source_family = article.category if article else "news"
        trust_score = source.trust_score if source else 0.5
        for mention in extractor.extract(
            text=summary.text,
            related_tickers=related_tickers,
            source_family=source_family or "news",
            trust_score=trust_score,
            article_id=summary.article_id,
            summary_id=summary.id,
            source_id=article.source_id if article else None,
        ):
            await repository.save(_to_model(mention))
            saved += 1

    return saved


async def score_market_signals(session: AsyncSession, settings: Settings) -> int:
    mention_repository = MarketMentionRepository(session)
    market_repository = MarketRepository(session)
    signal_repository = MarketSignalRepository(session)
    memory_repository = MarketThemeMemoryRepository(session)
    scorer = SignalScorer(settings)
    now = datetime.now(UTC)
    saved = 0
    memories = await memory_repository.list_recent(limit=50)

    for window, delta in SCORING_WINDOWS.items():
        aggregates = await mention_repository.aggregate(since=now - delta)
        snapshots = await market_repository.latest_snapshot_for_symbols(
            [aggregate.ticker for aggregate in aggregates if aggregate.ticker]
        )
        snapshots_by_symbol = {snapshot.symbol: snapshot for snapshot in snapshots}
        for aggregate in aggregates:
            theme_memory_count = sum(
                1
                for memory in memories
                if aggregate.theme and memory.theme == aggregate.theme.lower()
            )
            score = scorer.score(
                aggregate,
                window=window,
                market_snapshot=snapshots_by_symbol.get(aggregate.ticker or ""),
                baseline_mentions=max(aggregate.mention_count // 2, 1),
                theme_memory_count=theme_memory_count,
                now=now,
            )
            await signal_repository.save_snapshot(
                ticker=score.ticker,
                theme=score.theme,
                window=window,
                component_scores=score.components.as_dict(),
                total_score=score.total_score,
                evidence=score.evidence,
            )
            saved += 1
    return saved


async def prune_market_research_data(session: AsyncSession, settings: Settings) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=settings.signal_retention_days)
    mention_count = await MarketMentionRepository(session).delete_created_before(cutoff)
    signal_count = await MarketSignalRepository(session).delete_created_before(cutoff)
    return mention_count + signal_count


def _to_model(mention) -> MarketMention:
    return MarketMention(
        ticker=mention.ticker,
        theme=mention.theme,
        source_family=mention.source_family,
        source_id=mention.source_id,
        article_id=mention.article_id,
        summary_id=mention.summary_id,
        mention_count=mention.mention_count,
        trust_score=mention.trust_score,
        evidence_text=mention.evidence_text,
    )
