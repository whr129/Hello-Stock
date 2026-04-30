from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from news_agent.storage.models import Article, LongTermMemory, MarketSnapshot, Summary


@dataclass(frozen=True)
class RetrievedContext:
    articles: list[Article]
    summaries: list[Summary]
    memories: list[LongTermMemory]
    market_snapshots: list[MarketSnapshot]


class RetrievalService:
    """Initial lexical retrieval; pgvector semantic search can be added behind this interface."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def retrieve_for_brief(
        self,
        user_id: int,
        topics: list[str],
        tickers: list[str],
        limit: int = 10,
    ) -> RetrievedContext:
        article_stmt = (
            select(Article).order_by(Article.published_at.desc().nullslast()).limit(limit)
        )
        if topics:
            topic_filters = [Article.title.ilike(f"%{topic}%") for topic in topics]
            article_stmt = article_stmt.where(*topic_filters[:1])

        articles = list((await self.session.execute(article_stmt)).scalars())

        summaries = list(
            (
                await self.session.execute(
                    select(Summary)
                    .where((Summary.user_id == user_id) | (Summary.user_id.is_(None)))
                    .order_by(Summary.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )

        memories = list(
            (
                await self.session.execute(
                    select(LongTermMemory)
                    .where(LongTermMemory.user_id == user_id)
                    .order_by(LongTermMemory.updated_at.desc())
                    .limit(10)
                )
            ).scalars()
        )

        market_snapshots: list[MarketSnapshot] = []
        if tickers:
            for ticker in tickers:
                snapshot = (
                    await self.session.execute(
                        select(MarketSnapshot)
                        .where(MarketSnapshot.symbol == ticker)
                        .order_by(MarketSnapshot.captured_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if snapshot:
                    market_snapshots.append(snapshot)

        return RetrievedContext(
            articles=articles,
            summaries=summaries,
            memories=memories,
            market_snapshots=market_snapshots,
        )
