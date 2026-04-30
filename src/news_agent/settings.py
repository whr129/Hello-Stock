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
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    news_fetch_interval_seconds: int = Field(default=900, alias="NEWS_FETCH_INTERVAL_SECONDS")
    market_refresh_interval_seconds: int = Field(
        default=300, alias="MARKET_REFRESH_INTERVAL_SECONDS"
    )
    rss_fetch_timeout_seconds: int = Field(default=15, alias="RSS_FETCH_TIMEOUT_SECONDS")
    market_fetch_timeout_seconds: int = Field(default=20, alias="MARKET_FETCH_TIMEOUT_SECONDS")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")
    default_local_region: str = Field(default="Waterloo", alias="DEFAULT_LOCAL_REGION")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
