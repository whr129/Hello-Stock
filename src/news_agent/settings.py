from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    database_url: str = Field(
        default="postgresql+asyncpg://news_agent:news_agent@localhost:5432/news_agent",
        alias="DATABASE_URL",
    )
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    general_search_model: str = Field(default="", alias="GENERAL_SEARCH_MODEL")
    general_search_max_sources: int = Field(default=5, alias="GENERAL_SEARCH_MAX_SOURCES")
    general_search_timeout_seconds: int = Field(
        default=30, alias="GENERAL_SEARCH_TIMEOUT_SECONDS"
    )
    runtime_alert_telegram_chat_id: int = Field(default=0, alias="RUNTIME_ALERT_TELEGRAM_CHAT_ID")
    runtime_retention_days: int = Field(default=30, alias="RUNTIME_RETENTION_DAYS")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    news_fetch_interval_seconds: int = Field(default=900, alias="NEWS_FETCH_INTERVAL_SECONDS")
    market_refresh_interval_seconds: int = Field(
        default=300, alias="MARKET_REFRESH_INTERVAL_SECONDS"
    )
    rss_fetch_timeout_seconds: int = Field(default=15, alias="RSS_FETCH_TIMEOUT_SECONDS")
    market_fetch_timeout_seconds: int = Field(default=20, alias="MARKET_FETCH_TIMEOUT_SECONDS")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")
    default_local_region: str = Field(default="Waterloo", alias="DEFAULT_LOCAL_REGION")
    scheduler_tick_seconds: int = Field(default=60, alias="SCHEDULER_TICK_SECONDS")
    news_freshness_hours: int = Field(default=24, alias="NEWS_FRESHNESS_HOURS")
    summary_freshness_hours: int = Field(default=24, alias="SUMMARY_FRESHNESS_HOURS")
    snapshot_freshness_minutes: int = Field(default=15, alias="SNAPSHOT_FRESHNESS_MINUTES")
    article_retention_days: int = Field(default=30, alias="ARTICLE_RETENTION_DAYS")
    snapshot_retention_days: int = Field(default=7, alias="SNAPSHOT_RETENTION_DAYS")
    job_run_retention_days: int = Field(default=30, alias="JOB_RUN_RETENTION_DAYS")
    short_term_memory_window_size: int = Field(default=20, alias="SHORT_TERM_MEMORY_WINDOW_SIZE")
    short_term_memory_expiry_minutes: int = Field(
        default=60,
        alias="SHORT_TERM_MEMORY_EXPIRY_MINUTES",
    )
    long_term_memory_batch_size: int = Field(default=20, alias="LONG_TERM_MEMORY_BATCH_SIZE")
    long_term_memory_top_k: int = Field(default=5, alias="LONG_TERM_MEMORY_TOP_K")
    memory_candidates_per_batch: int = Field(default=6, alias="MEMORY_CANDIDATES_PER_BATCH")
    memory_job_max_retries: int = Field(default=3, alias="MEMORY_JOB_MAX_RETRIES")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
