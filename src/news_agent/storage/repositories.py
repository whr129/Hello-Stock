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
    RuntimeAlert,
    RuntimeError,
    RuntimeRun,
    RuntimeStep,
    ShortTermSession,
    Source,
    Summary,
    SummaryEmbedding,
    User,
    WatchedTicker,
)


def build_source_locator(provider: str, external_account: str) -> str:
    normalized_provider = provider.strip().lower()
    normalized_account = external_account.strip()
    if normalized_provider == "rss":
        return normalized_account
    return f"{normalized_provider}://{normalized_account}"


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

    async def set_timezone(self, user_id: int, timezone: str) -> User | None:
        await self.session.execute(update(User).where(User.id == user_id).values(timezone=timezone))
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

    async def set_delivery_time(self, user_id: int, delivery_time: str) -> Preference:
        preference = await self.get_for_user(user_id)
        preference.delivery_time = delivery_time
        await self.session.flush()
        return preference

    async def clear_delivery_time(self, user_id: int) -> Preference:
        preference = await self.get_for_user(user_id)
        preference.delivery_time = None
        await self.session.flush()
        return preference

    async def mark_daily_recap_sent(self, user_id: int, sent_at: datetime) -> Preference:
        preference = await self.get_for_user(user_id)
        preference.last_daily_recap_sent_at = sent_at
        await self.session.flush()
        return preference

    async def list_with_delivery_time(self) -> list[tuple[User, Preference]]:
        result = await self.session.execute(
            select(User, Preference)
            .join(Preference, Preference.user_id == User.id)
            .where(Preference.delivery_time.is_not(None))
        )
        return list(result.all())


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

    async def get_by_id(self, source_id: int) -> Source | None:
        result = await self.session.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one_or_none()

    async def add_source(
        self,
        *,
        name: str,
        provider: str,
        external_account: str,
        category: str = "general",
        owner_user_id: int | None = None,
        config: dict | None = None,
        field_mapping: dict | None = None,
        fetch_mode: str | None = None,
    ) -> Source:
        normalized_provider = provider.strip().lower()
        normalized_account = external_account.strip()
        locator = build_source_locator(normalized_provider, normalized_account)
        existing = await self.session.execute(select(Source).where(Source.url == locator))
        source = existing.scalar_one_or_none()
        if source:
            source.enabled = True
            source.provider = normalized_provider
            source.external_account = normalized_account
            if config:
                source.config = {**dict(source.config or {}), **config}
            if field_mapping:
                source.field_mapping = {**dict(source.field_mapping or {}), **field_mapping}
            if fetch_mode is not None:
                source.fetch_mode = fetch_mode
            await self.session.flush()
            return source

        source = Source(
            name=name,
            url=locator,
            provider=normalized_provider,
            external_account=normalized_account,
            config=dict(config or {}),
            field_mapping=dict(field_mapping or {}),
            fetch_mode=fetch_mode or ("rss" if normalized_provider == "rss" else None),
            category=category,
            owner_user_id=owner_user_id,
        )
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

    async def update_config_field(self, source_id: int, key: str, value: object) -> Source | None:
        source = await self.get_by_id(source_id)
        if source is None:
            return None
        config = dict(source.config or {})
        config[key] = value
        source.config = config
        await self.session.flush()
        return source

    async def update_field_mapping(self, source_id: int, key: str, value: str) -> Source | None:
        source = await self.get_by_id(source_id)
        if source is None:
            return None
        field_mapping = dict(source.field_mapping or {})
        field_mapping[key] = value
        source.field_mapping = field_mapping
        await self.session.flush()
        return source

    async def mark_fetch_result(
        self,
        source_id: int,
        *,
        fetched_at: datetime,
        success: bool,
        error: str | None = None,
    ) -> None:
        source = await self.get_by_id(source_id)
        if source is None:
            return
        source.last_fetched_at = fetched_at
        if success:
            source.last_success_at = fetched_at
            source.last_error = None
        else:
            source.last_error = error
        await self.session.flush()

    async def ensure_default_sources(self) -> list[Source]:
        defaults = [
            (
                "Reuters Business",
                "rss",
                "https://feeds.reuters.com/reuters/businessNews",
                "markets",
            ),
            ("BBC World", "rss", "https://feeds.bbci.co.uk/news/world/rss.xml", "world"),
            ("CBC Top Stories", "rss", "https://www.cbc.ca/cmlink/rss-topstories", "local"),
            (
                "MarketWatch Top Stories",
                "rss",
                "https://feeds.marketwatch.com/marketwatch/topstories/",
                "markets",
            ),
        ]
        sources: list[Source] = []
        for name, provider, external_account, category in defaults:
            sources.append(
                await self.add_source(
                    name=name,
                    provider=provider,
                    external_account=external_account,
                    category=category,
                    config={"feed_url": external_account},
                    fetch_mode="rss",
                )
            )
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
        author: str | None = None,
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
            author=author,
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

    async def delete_created_before(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(Article).where(Article.created_at < cutoff).returning(Article.id)
        )
        await self.session.flush()
        rows = result.scalars().all()
        return len(rows)


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

    async def delete_captured_before(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(MarketSnapshot).where(MarketSnapshot.captured_at < cutoff).returning(MarketSnapshot.id)
        )
        await self.session.flush()
        rows = result.scalars().all()
        return len(rows)


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

    async def delete_created_before(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(Summary).where(Summary.created_at < cutoff).returning(Summary.id)
        )
        await self.session.flush()
        rows = result.scalars().all()
        return len(rows)


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

    async def has_running_job(self) -> bool:
        result = await self.session.execute(
            select(JobRun.id).where(JobRun.status == "running").limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def recover_stale_running_jobs(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            update(JobRun)
            .where(JobRun.status == "running")
            .where(JobRun.started_at < cutoff)
            .values(
                status="abandoned",
                error_message="Recovered stale running job",
                completed_at=datetime.now(UTC),
            )
            .returning(JobRun.id)
        )
        await self.session.flush()
        return len(result.scalars().all())

    async def delete_started_before(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(JobRun).where(JobRun.started_at < cutoff).returning(JobRun.id)
        )
        await self.session.flush()
        rows = result.scalars().all()
        return len(rows)


class RuntimeRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self,
        *,
        workflow: str,
        trigger: str | None = None,
        telegram_user_id: int | None = None,
        chat_id: int | None = None,
        metadata: dict | None = None,
    ) -> RuntimeRun:
        item = RuntimeRun(
            workflow=workflow,
            trigger=trigger,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            status="running",
            run_metadata=dict(metadata or {}),
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def finish(self, run_id: int, *, status: str, summary: str | None = None) -> RuntimeRun | None:
        result = await self.session.execute(select(RuntimeRun).where(RuntimeRun.id == run_id))
        item = result.scalar_one_or_none()
        if item is None:
            return None
        item.status = status
        item.summary = summary
        item.completed_at = datetime.now(UTC)
        await self.session.flush()
        return item

    async def get(self, run_id: int) -> RuntimeRun | None:
        result = await self.session.execute(select(RuntimeRun).where(RuntimeRun.id == run_id))
        return result.scalar_one_or_none()

    async def list_recent(
        self,
        *,
        limit: int = 5,
        workflow: str | None = None,
        exclude_run_id: int | None = None,
    ) -> list[RuntimeRun]:
        stmt = select(RuntimeRun)
        if workflow:
            stmt = stmt.where(RuntimeRun.workflow == workflow)
        if exclude_run_id:
            stmt = stmt.where(RuntimeRun.id != exclude_run_id)
        stmt = stmt.order_by(RuntimeRun.started_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def delete_started_before(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(RuntimeRun).where(RuntimeRun.started_at < cutoff).returning(RuntimeRun.id)
        )
        await self.session.flush()
        return len(result.scalars().all())


class RuntimeStepRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self,
        *,
        run_id: int,
        workflow: str,
        step_name: str,
        step_type: str,
        parent_step_id: int | None = None,
        metadata: dict | None = None,
    ) -> RuntimeStep:
        item = RuntimeStep(
            run_id=run_id,
            workflow=workflow,
            step_name=step_name,
            step_type=step_type,
            parent_step_id=parent_step_id,
            status="running",
            step_metadata=dict(metadata or {}),
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def finish(
        self,
        step_id: int,
        *,
        status: str,
        error_message: str | None = None,
        metadata: dict | None = None,
    ) -> RuntimeStep | None:
        result = await self.session.execute(select(RuntimeStep).where(RuntimeStep.id == step_id))
        item = result.scalar_one_or_none()
        if item is None:
            return None
        item.status = status
        item.error_message = error_message
        item.completed_at = datetime.now(UTC)
        elapsed = (item.completed_at - item.started_at).total_seconds()
        item.duration_ms = max(int(elapsed * 1000), 0)
        if metadata:
            item.step_metadata = {**dict(item.step_metadata or {}), **metadata}
        await self.session.flush()
        return item

    async def list_for_run(self, run_id: int) -> list[RuntimeStep]:
        result = await self.session.execute(
            select(RuntimeStep).where(RuntimeStep.run_id == run_id).order_by(RuntimeStep.id)
        )
        return list(result.scalars())

    async def get_for_run(self, run_id: int, step_name: str) -> RuntimeStep | None:
        result = await self.session.execute(
            select(RuntimeStep)
            .where(RuntimeStep.run_id == run_id)
            .where(RuntimeStep.step_name == step_name)
            .order_by(RuntimeStep.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class RuntimeErrorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        run_id: int,
        workflow: str,
        step_name: str,
        error_message: str,
        step_id: int | None = None,
        metadata: dict | None = None,
    ) -> RuntimeError:
        item = RuntimeError(
            run_id=run_id,
            step_id=step_id,
            workflow=workflow,
            step_name=step_name,
            error_message=error_message,
            error_metadata=dict(metadata or {}),
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_recent(self, *, limit: int = 10) -> list[RuntimeError]:
        result = await self.session.execute(
            select(RuntimeError).order_by(RuntimeError.created_at.desc()).limit(limit)
        )
        return list(result.scalars())

    async def list_for_run(self, run_id: int) -> list[RuntimeError]:
        result = await self.session.execute(
            select(RuntimeError)
            .where(RuntimeError.run_id == run_id)
            .order_by(RuntimeError.id)
        )
        return list(result.scalars())

    async def search_recent(self, query: str, *, limit: int = 10) -> list[RuntimeError]:
        pattern = f"%{query.lower()}%"
        result = await self.session.execute(
            select(RuntimeError)
            .where(RuntimeError.error_message.ilike(pattern))
            .order_by(RuntimeError.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())


class RuntimeAlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        run_id: int,
        channel: str,
        status: str,
        message_text: str,
        target: str | None = None,
        error_id: int | None = None,
        delivered_at: datetime | None = None,
    ) -> RuntimeAlert:
        item = RuntimeAlert(
            run_id=run_id,
            error_id=error_id,
            channel=channel,
            status=status,
            message_text=message_text,
            target=target,
            delivered_at=delivered_at,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_recent(self, *, limit: int = 10) -> list[RuntimeAlert]:
        result = await self.session.execute(
            select(RuntimeAlert).order_by(RuntimeAlert.created_at.desc()).limit(limit)
        )
        return list(result.scalars())


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
