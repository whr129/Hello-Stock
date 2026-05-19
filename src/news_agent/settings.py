from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

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
    answer_reflection_enabled: bool = Field(default=True, alias="ANSWER_REFLECTION_ENABLED")
    answer_reflection_max_retries: int = Field(default=1, alias="ANSWER_REFLECTION_MAX_RETRIES")
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
    financial_advice_terms: str = Field(
        default="you should buy,you should sell,guaranteed,risk-free,sure profit",
        alias="FINANCIAL_ADVICE_TERMS",
    )
    financial_guardrail_disclaimer: str = Field(
        default="This is informational only, not financial advice.",
        alias="FINANCIAL_GUARDRAIL_DISCLAIMER",
    )
    market_impact_allowed_categories: str = Field(
        default="tech,macro,policy,regulatory,earnings,filings,finance,markets",
        alias="MARKET_IMPACT_ALLOWED_CATEGORIES",
    )
    market_impact_keywords: str = Field(
        default=(
            "AI,semiconductor,semiconductors,rates,CPI,tariff,tariffs,sanction,sanctions,"
            "earnings,M&A,guidance,capex,"
            "regulation,acquisition,merger,antitrust,bank,bond,company,economy,"
            "export control,fed,filing,forecast,inflation,IPO,market,nasdaq,profit,"
            "revenue,sales,shares,stock,treasury"
        ),
        alias="MARKET_IMPACT_KEYWORDS",
    )
    market_impact_reject_terms: str = Field(
        default=(
            "box office,celebrity,concert,credit card points,fashion,inherited a house,"
            "inherited house,money moves,movie review,personal finance,recipe,restaurant,"
            "sports,tournament,travel tips,tv show,weather forecast"
        ),
        alias="MARKET_IMPACT_REJECT_TERMS",
    )
    market_impact_minimum_confidence: float = Field(
        default=0.8,
        alias="MARKET_IMPACT_MINIMUM_CONFIDENCE",
    )
    llm_market_impact_classification_enabled: bool = Field(
        default=False,
        alias="LLM_MARKET_IMPACT_CLASSIFICATION_ENABLED",
    )
    llm_market_impact_classification_threshold: float = Field(
        default=0.8,
        alias="LLM_MARKET_IMPACT_CLASSIFICATION_THRESHOLD",
    )
    scheduler_tick_seconds: int = Field(default=60, alias="SCHEDULER_TICK_SECONDS")
    news_freshness_hours: int = Field(default=24, alias="NEWS_FRESHNESS_HOURS")
    summary_freshness_hours: int = Field(default=24, alias="SUMMARY_FRESHNESS_HOURS")
    snapshot_freshness_minutes: int = Field(default=15, alias="SNAPSHOT_FRESHNESS_MINUTES")
    article_retention_days: int = Field(default=30, alias="ARTICLE_RETENTION_DAYS")
    snapshot_retention_days: int = Field(default=30, alias="SNAPSHOT_RETENTION_DAYS")
    market_universe_symbols: str = Field(
        default="SPY,QQQ,DIA,IWM,AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,AVGO,JPM,V,LLY,UNH,XOM",
        alias="MARKET_UNIVERSE_SYMBOLS",
    )
    market_research_theme_config: str = Field(
        default=(
            '{"AI infrastructure":["ai","artificial intelligence","gpu","data center",'
            '"datacenter"],'
            '"memory chips":["hbm","dram","nand","memory chip","memory demand"],'
            '"cloud capex":["cloud capex","capital expenditure","hyperscaler","cloud spending"],'
            '"rates":["fed","treasury yield","rate cut","rate hike","inflation","cpi"],'
            '"regional banks":["regional bank","deposit","commercial real estate"],'
            '"energy supply":["oil","natural gas","opec","lng","energy supply"],'
            '"obesity drugs":["glp-1","obesity","weight loss drug"],'
            '"defense spending":["defense","missile","military contract","geopolitical"]}'
        ),
        alias="MARKET_RESEARCH_THEME_CONFIG",
    )
    market_research_blocked_tickers: str = Field(
        default="AI,CEO,CFO,CPA,ETF,GDP,HBM,IPO,LLC,SEC,THIS,USA",
        alias="MARKET_RESEARCH_BLOCKED_TICKERS",
    )
    market_research_allowed_single_letter_tickers: str = Field(
        default="",
        alias="MARKET_RESEARCH_ALLOWED_SINGLE_LETTER_TICKERS",
    )
    market_research_non_entity_terms: str = Field(
        default="this,that,there,here,said,will,with,from,into,over,under",
        alias="MARKET_RESEARCH_NON_ENTITY_TERMS",
    )
    source_default_fetch_interval_seconds: int = Field(
        default=900,
        alias="SOURCE_DEFAULT_FETCH_INTERVAL_SECONDS",
    )
    default_sources_json: str = Field(default="[]", alias="DEFAULT_SOURCES_JSON")
    source_max_items_per_fetch: int = Field(default=50, alias="SOURCE_MAX_ITEMS_PER_FETCH")
    source_max_item_age_hours: int = Field(default=72, alias="SOURCE_MAX_ITEM_AGE_HOURS")
    signal_retention_days: int = Field(default=30, alias="SIGNAL_RETENTION_DAYS")
    signal_alert_threshold: float = Field(default=75.0, alias="SIGNAL_ALERT_THRESHOLD")
    signal_alert_cooldown_minutes: int = Field(
        default=360, alias="SIGNAL_ALERT_COOLDOWN_MINUTES"
    )
    signal_weight_mention_velocity: float = Field(
        default=1.0, alias="SIGNAL_WEIGHT_MENTION_VELOCITY"
    )
    signal_weight_source_diversity: float = Field(
        default=1.0, alias="SIGNAL_WEIGHT_SOURCE_DIVERSITY"
    )
    signal_weight_recency: float = Field(default=1.0, alias="SIGNAL_WEIGHT_RECENCY")
    signal_weight_semantic_similarity: float = Field(
        default=1.0, alias="SIGNAL_WEIGHT_SEMANTIC_SIMILARITY"
    )
    signal_weight_price_momentum: float = Field(
        default=1.0, alias="SIGNAL_WEIGHT_PRICE_MOMENTUM"
    )
    signal_weight_volume: float = Field(default=1.0, alias="SIGNAL_WEIGHT_VOLUME")
    signal_weight_theme_persistence: float = Field(
        default=1.0, alias="SIGNAL_WEIGHT_THEME_PERSISTENCE"
    )
    signal_weight_trust: float = Field(default=1.0, alias="SIGNAL_WEIGHT_TRUST")
    social_signals_enabled: bool = Field(default=False, alias="SOCIAL_SIGNALS_ENABLED")
    llm_mention_extraction_enabled: bool = Field(
        default=False, alias="LLM_MENTION_EXTRACTION_ENABLED"
    )
    job_run_retention_days: int = Field(default=30, alias="JOB_RUN_RETENTION_DAYS")
    short_term_memory_window_size: int = Field(default=20, alias="SHORT_TERM_MEMORY_WINDOW_SIZE")
    short_term_memory_expiry_minutes: int = Field(
        default=43200,
        alias="SHORT_TERM_MEMORY_EXPIRY_MINUTES",
    )
    conversation_event_retention_days: int = Field(
        default=30,
        alias="CONVERSATION_EVENT_RETENTION_DAYS",
    )
    long_term_memory_batch_size: int = Field(default=20, alias="LONG_TERM_MEMORY_BATCH_SIZE")
    long_term_memory_top_k: int = Field(default=5, alias="LONG_TERM_MEMORY_TOP_K")
    memory_candidates_per_batch: int = Field(default=6, alias="MEMORY_CANDIDATES_PER_BATCH")
    memory_job_max_retries: int = Field(default=3, alias="MEMORY_JOB_MAX_RETRIES")
    eval_llm_enabled: bool = Field(default=False, alias="EVAL_LLM_ENABLED")
    eval_model: str = Field(default="", alias="EVAL_MODEL")
    eval_max_cases: int = Field(default=50, alias="EVAL_MAX_CASES")
    eval_output_path: str = Field(default="reports/eval", alias="EVAL_OUTPUT_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
