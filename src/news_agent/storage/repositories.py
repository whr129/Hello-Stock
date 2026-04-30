from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID as PythonUUID

from sqlalchemy import delete, distinct, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from news_agent.settings import Settings
from news_agent.storage.models import (
    Article,
    ArticleEmbedding,
    JobRun,
    LongTermMemory,
    MarketSnapshot,
    MemoryEmbedding,
    MemoryType,
    Preference,
    ShortTermSession,
    Source,
    Summary,
    SummaryEmbedding,
    User,
    WatchedTicker,
)


class UserRepository:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def get_or_create_user(self, telegram_user_id: int) -> User:
        result = await self.session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        user = result.scalar_one_or_none()
        if user:
            return user

        user = User(
            telegram_user_id=telegram_user_id,
            local_region=self.settings.default_local_region,
        )
        self.session.add(user)
        await self.session.flush()
        self.session.add(Preference(user_id=user.id, topics=[]))
        await self.session.flush()
        return user

    async def set_local_region(self, user_id: int, local_region: str) -> User | None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(local_region=local_region)
        )
        await self.session.flush()
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


class PreferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: int) -> Preference:
        result = await self.session.execute(select(Preference).where(Preference.user_id == user_id))
        preference = result.scalar_one_or_none()
        if preference is None:
            preference = Preference(user_id=user_id)
            self.session.add(preference)
            await self.session.flush()
        return preference

    async def set_topics(self, user_id: int, topics: Sequence[str]) -> Preference:
        preference = await self.get_for_user(user_id)
        preference.topics = [topic.lower() for topic in topics]
        await self.session.flush()
        return preference


class TickerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: int) -> list[str]:
        result = await self.session.execute(
            select(WatchedTicker.symbol)
            .where(WatchedTicker.user_id == user_id)
            .order_by(WatchedTicker.symbol)
        )
        return list(result.scalars())

    async def list_all_symbols(self) -> list[str]:
        result = await self.session.execute(
            select(distinct(WatchedTicker.symbol)).order_by(WatchedTicker.symbol)
        )
        return list(result.scalars())

    async def add_many(self, user_id: int, symbols: Sequence[str]) -> list[str]:
        existing = set(await self.list_for_user(user_id))
        added: list[str] = []
        for symbol in {item.upper() for item in symbols if item.strip()}:
            if symbol not in existing:
                self.session.add(WatchedTicker(user_id=user_id, symbol=symbol))
                added.append(symbol)
        await self.session.flush()
        return sorted(added)

    async def remove_many(self, user_id: int, symbols: Sequence[str]) -> list[str]:
        normalized = [item.upper() for item in symbols]
        await self.session.execute(
            delete(WatchedTicker).where(
                WatchedTicker.user_id == user_id, WatchedTicker.symbol.in_(normalized)
            )
        )
        await self.session.flush()
        return sorted(normalized)


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_enabled(self, user_id: int | None = None) -> list[Source]:
        result = await self.session.execute(
            select(Source)
            .where(Source.enabled.is_(True))
            .where((Source.owner_user_id.is_(None)) | (Source.owner_user_id == user_id))
            .order_by(Source.name)
        )
        return list(result.scalars())

    async def list_all_enabled(self) -> list[Source]:
        result = await self.session.execute(
            select(Source).where(Source.enabled.is_(True)).order_by(Source.name)
        )
        return list(result.scalars())

    async def add_source(
        self, name: str, url: str, category: str = "general", owner_user_id: int | None = None
    ) -> Source:
        existing = await self.session.execute(select(Source).where(Source.url == url))
        source = existing.scalar_one_or_none()
        if source:
            source.enabled = True
            await self.session.flush()
            return source

        source = Source(name=name, url=url, category=category, owner_user_id=owner_user_id)
        self.session.add(source)
        await self.session.flush()
        return source

    async def disable_source(self, source_id: int, owner_user_id: int) -> bool:
        result = await self.session.execute(
            update(Source)
            .where(Source.id == source_id)
            .where((Source.owner_user_id == owner_user_id) | (Source.owner_user_id.is_(None)))
            .values(enabled=False)
            .returning(Source.id)
        )
        await self.session.flush()
        return result.scalar_one_or_none() is not None

    async def ensure_default_sources(self) -> list[Source]:
        defaults = [
            ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews", "markets"),
            ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "world"),
            ("CBC Top Stories", "https://www.cbc.ca/cmlink/rss-topstories", "local"),
            (
                "MarketWatch Top Stories",
                "https://feeds.marketwatch.com/marketwatch/topstories/",
                "markets",
            ),
        ]
        sources: list[Source] = []
        for name, url, category in defaults:
            sources.append(await self.add_source(name=name, url=url, category=category))
        return sources


class ArticleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_article(
        self,
        *,
        source_id: int | None,
        url: str,
        title: str,
        content_hash: str,
        category: str,
        published_at: datetime | None = None,
        extracted_text: str | None = None,
        related_tickers: Sequence[str] | None = None,
    ) -> tuple[Article, bool]:
        result = await self.session.execute(select(Article).where(Article.url == url))
        article = result.scalar_one_or_none()
        if article:
            return article, False

        article = Article(
            source_id=source_id,
            url=url,
            title=title,
            published_at=published_at,
            content_hash=content_hash,
            category=category,
            extracted_text=extracted_text,
            related_tickers=[ticker.upper() for ticker in related_tickers or []],
        )
        self.session.add(article)
        await self.session.flush()
        return article, True

    async def list_without_summaries(self, limit: int = 20) -> list[Article]:
        summarized = select(Summary.article_id).where(Summary.article_id.is_not(None))
        result = await self.session.execute(
            select(Article)
            .where(Article.id.not_in(summarized))
            .order_by(Article.published_at.desc().nullslast(), Article.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())


class MarketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_snapshot(
        self,
        symbol: str,
        price: float | None,
        percent_change: float | None,
        indicators: dict,
        timeframe: str = "1d",
    ) -> MarketSnapshot:
        snapshot = MarketSnapshot(
            symbol=symbol.upper(),
            price=price,
            percent_change=percent_change,
            indicators=indicators,
            timeframe=timeframe,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot


class SummaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def has_article_summary(self, article_id: int) -> bool:
        result = await self.session.execute(
            select(Summary.id).where(
                Summary.article_id == article_id,
                Summary.summary_type == "article",
            )
        )
        return result.scalar_one_or_none() is not None

    async def save_article_summary(
        self,
        *,
        article_id: int,
        text: str,
        model_name: str,
        model_provider: str = "openai",
    ) -> Summary:
        summary = Summary(
            article_id=article_id,
            summary_type="article",
            text=text,
            model_provider=model_provider,
            model_name=model_name,
        )
        self.session.add(summary)
        await self.session.flush()
        return summary


class EmbeddingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_article_embedding(
        self, article_id: int, embedding: list[float], embedding_model: str
    ) -> ArticleEmbedding:
        item = ArticleEmbedding(
            article_id=article_id,
            embedding=embedding,
            embedding_model=embedding_model,
            chunk_metadata={},
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def save_summary_embedding(
        self, summary_id: int, embedding: list[float], embedding_model: str
    ) -> SummaryEmbedding:
        item = SummaryEmbedding(
            summary_id=summary_id,
            embedding=embedding,
            embedding_model=embedding_model,
            chunk_metadata={},
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def save_memory_embedding(
        self, memory_id: int, embedding: list[float], embedding_model: str
    ) -> MemoryEmbedding:
        item = MemoryEmbedding(
            memory_id=memory_id,
            embedding=embedding,
            embedding_model=embedding_model,
        )
        self.session.add(item)
        await self.session.flush()
        return item


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(self, job_type: str) -> JobRun:
        job = JobRun(job_type=job_type, status="running")
        self.session.add(job)
        await self.session.flush()
        return job

    async def finish(self, job: JobRun, status: str, error_message: str | None = None) -> None:
        job.status = status
        job.error_message = error_message
        job.completed_at = datetime.now(UTC)
        await self.session.flush()


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: int) -> list[LongTermMemory]:
        result = await self.session.execute(
            select(LongTermMemory)
            .where(LongTermMemory.user_id == user_id)
            .order_by(LongTermMemory.updated_at.desc())
            .limit(20)
        )
        return list(result.scalars())

    async def remember(
        self,
        user_id: int,
        text: str,
        memory_type: MemoryType = MemoryType.EXPLICIT,
        source: str = "user",
        confidence: float = 1.0,
    ) -> LongTermMemory:
        memory = LongTermMemory(
            user_id=user_id,
            memory_text=text,
            memory_type=memory_type.value,
            source=source,
            confidence=confidence,
        )
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def reset_learned(self, user_id: int) -> None:
        await self.session.execute(
            delete(LongTermMemory).where(
                LongTermMemory.user_id == user_id,
                LongTermMemory.memory_type != MemoryType.EXPLICIT.value,
            )
        )
        await self.session.flush()

    async def forget(self, user_id: int, public_id: str) -> bool:
        try:
            memory_uuid = PythonUUID(public_id)
        except ValueError:
            return False

        result = await self.session.execute(
            delete(LongTermMemory)
            .where(LongTermMemory.user_id == user_id)
            .where(LongTermMemory.public_id == memory_uuid)
            .returning(LongTermMemory.id)
        )
        await self.session.flush()
        return result.scalar_one_or_none() is not None


class ShortTermSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_state(self, chat_id: int) -> dict:
        item = await self.session.get(ShortTermSession, chat_id)
        if item is None:
            return {}
        return dict(item.state or {})

    async def save_state(self, chat_id: int, state: dict, expires_at: datetime) -> None:
        item = await self.session.get(ShortTermSession, chat_id)
        if item is None:
            item = ShortTermSession(chat_id=chat_id, state=state, expires_at=expires_at)
            self.session.add(item)
        else:
            item.state = state
            item.expires_at = expires_at
        await self.session.flush()
