from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from news_agent.research.schemas import MarketContext, ResearchPlan
from news_agent.storage.models import (
    Article,
    MarketMention,
    MarketSignalSnapshot,
    MarketSnapshot,
    MarketThemeMemory,
    Summary,
)


async def retrieve_market_context(session: AsyncSession, plan: ResearchPlan) -> MarketContext:
    cutoff = datetime.now(UTC) - _horizon_delta(plan.research_horizon)
    tickers = [ticker.upper() for ticker in plan.entities.tickers]
    themes = [theme.lower() for theme in plan.entities.themes]
    limit = max(plan.constraints.max_candidates * 5, 10)

    article_stmt = select(Article).where(
        (Article.published_at >= cutoff) | (Article.created_at >= cutoff)
    )
    article_filters = []
    for ticker in tickers:
        article_filters.append(Article.title.ilike(f"%{ticker}%"))
    for theme in themes:
        article_filters.append(Article.title.ilike(f"%{theme}%"))
    if article_filters:
        article_stmt = article_stmt.where(or_(*article_filters))
    article_stmt = article_stmt.order_by(Article.published_at.desc().nullslast()).limit(limit)

    summary_stmt = select(Summary).where(Summary.created_at >= cutoff)
    summary_filters = [Summary.text.ilike(f"%{item}%") for item in [*tickers, *themes]]
    if summary_filters:
        summary_stmt = summary_stmt.where(or_(*summary_filters))
    summary_stmt = summary_stmt.order_by(Summary.created_at.desc()).limit(limit)

    mention_stmt = select(MarketMention).where(MarketMention.created_at >= cutoff)
    if tickers:
        mention_stmt = mention_stmt.where(MarketMention.ticker.in_(tickers))
    if themes:
        mention_stmt = mention_stmt.where(MarketMention.theme.in_(themes))
    mention_stmt = mention_stmt.order_by(MarketMention.created_at.desc()).limit(limit)

    signal_stmt = select(MarketSignalSnapshot).where(MarketSignalSnapshot.created_at >= cutoff)
    if tickers:
        signal_stmt = signal_stmt.where(MarketSignalSnapshot.ticker.in_(tickers))
    signal_stmt = signal_stmt.order_by(MarketSignalSnapshot.total_score.desc()).limit(limit)

    market_snapshots = []
    for ticker in tickers:
        snapshot = (
            await session.execute(
                select(MarketSnapshot)
                .where(MarketSnapshot.symbol == ticker)
                .order_by(MarketSnapshot.captured_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if snapshot:
            market_snapshots.append(snapshot)

    memories = list(
        (
            await session.execute(
                select(MarketThemeMemory)
                .order_by(MarketThemeMemory.last_seen_at.desc().nullslast())
                .limit(10)
            )
        ).scalars()
    )

    return MarketContext(
        articles=list((await session.execute(article_stmt)).scalars()),
        summaries=list((await session.execute(summary_stmt)).scalars()),
        mentions=list((await session.execute(mention_stmt)).scalars()),
        signal_snapshots=list((await session.execute(signal_stmt)).scalars()),
        market_snapshots=market_snapshots,
        theme_memories=memories,
    )


def _horizon_delta(horizon: str) -> timedelta:
    if horizon == "intraday":
        return timedelta(hours=24)
    if horizon == "7d":
        return timedelta(days=7)
    return timedelta(days=30)
