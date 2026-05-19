import json
import re
from collections.abc import Iterable

from openai import APIError, APITimeoutError, AsyncOpenAI

from news_agent.research.schemas import ExtractedMention
from news_agent.settings import Settings

DEFAULT_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI infrastructure": ("ai", "artificial intelligence", "gpu", "data center", "datacenter"),
    "memory chips": ("hbm", "dram", "nand", "memory chip", "memory demand"),
    "cloud capex": ("cloud capex", "capital expenditure", "hyperscaler", "cloud spending"),
    "rates": ("fed", "treasury yield", "rate cut", "rate hike", "inflation", "cpi"),
    "regional banks": ("regional bank", "deposit", "commercial real estate"),
    "energy supply": ("oil", "natural gas", "opec", "lng", "energy supply"),
    "obesity drugs": ("glp-1", "obesity", "weight loss drug"),
    "defense spending": ("defense", "missile", "military contract", "geopolitical"),
}
TOKEN_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9&.-]{1,}\b")
CASHTAG_PATTERN = re.compile(r"\$([A-Za-z]{1,5})(?:\b|$)")
BARE_UPPERCASE_TICKER_PATTERN = re.compile(r"\b([A-Z]{2,5})\b")
DEFAULT_NON_ENTITY_TICKERS = {
    "AI",
    "CEO",
    "CFO",
    "CPA",
    "ETF",
    "GDP",
    "HBM",
    "IPO",
    "LLC",
    "SEC",
    "THIS",
    "USA",
}


class MentionExtractor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self.theme_keywords = _theme_keywords_from_settings(settings)
        self.blocked_tickers = _csv_set(
            settings.market_research_blocked_tickers if settings else ""
        ) or DEFAULT_NON_ENTITY_TICKERS
        self.allowed_single_letter_tickers = _csv_set(
            settings.market_research_allowed_single_letter_tickers if settings else ""
        )
        self.non_entity_terms = _csv_set(
            settings.market_research_non_entity_terms if settings else ""
        )
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key)
            if settings and settings.llm_mention_extraction_enabled and settings.openai_api_key
            else None
        )

    async def extract_async(
        self,
        *,
        text: str,
        related_tickers: Iterable[str] = (),
        source_family: str = "news",
        trust_score: float = 0.5,
        article_id: int | None = None,
        summary_id: int | None = None,
        source_id: int | None = None,
    ) -> list[ExtractedMention]:
        deterministic = self.extract(
            text=text,
            related_tickers=related_tickers,
            source_family=source_family,
            trust_score=trust_score,
            article_id=article_id,
            summary_id=summary_id,
            source_id=source_id,
        )
        if deterministic or not self.client:
            return deterministic
        llm_mentions = await self._extract_with_llm(
            text=text,
            source_family=source_family,
            trust_score=trust_score,
            article_id=article_id,
            summary_id=summary_id,
            source_id=source_id,
        )
        return llm_mentions

    def extract(
        self,
        *,
        text: str,
        related_tickers: Iterable[str] = (),
        source_family: str = "news",
        trust_score: float = 0.5,
        article_id: int | None = None,
        summary_id: int | None = None,
        source_id: int | None = None,
    ) -> list[ExtractedMention]:
        clean_text = " ".join(text.split())
        extracted = [
            *self._extract_tickers(clean_text),
            *(_normalize_related_ticker(ticker) for ticker in related_tickers),
        ]
        tickers = sorted(
            dict.fromkeys(
                ticker for ticker in extracted if ticker and ticker not in self.blocked_tickers
            )
        )
        themes = extract_themes(clean_text, self.theme_keywords)
        mentions: list[ExtractedMention] = []
        evidence = _evidence_snippet(clean_text)

        if not tickers and not themes:
            return []

        for ticker in tickers or [None]:
            ticker_count = _ticker_count(clean_text, ticker) if ticker else 1
            if themes:
                for theme in themes:
                    mentions.append(
                        ExtractedMention(
                            ticker=ticker,
                            theme=theme,
                            mention_count=max(ticker_count, 1),
                            evidence_text=evidence,
                            source_family=source_family,
                            trust_score=trust_score,
                            article_id=article_id,
                            summary_id=summary_id,
                            source_id=source_id,
                        )
                    )
            else:
                mentions.append(
                    ExtractedMention(
                        ticker=ticker,
                        theme=None,
                        mention_count=max(ticker_count, 1),
                        evidence_text=evidence,
                        source_family=source_family,
                        trust_score=trust_score,
                        article_id=article_id,
                        summary_id=summary_id,
                        source_id=source_id,
                    )
                )

        if themes and not tickers:
            return [
                ExtractedMention(
                    ticker=None,
                    theme=theme,
                    mention_count=1,
                    evidence_text=evidence,
                    source_family=source_family,
                    trust_score=trust_score,
                    article_id=article_id,
                    summary_id=summary_id,
                    source_id=source_id,
                )
                for theme in themes
            ]
        return mentions

    def _extract_tickers(self, text: str) -> list[str]:
        cashtags = [
            match.group(1).upper()
            for match in CASHTAG_PATTERN.finditer(text)
            if self._looks_like_ticker(match.group(1), allow_one_letter=True)
        ]
        bare = [
            match.group(1).upper()
            for match in BARE_UPPERCASE_TICKER_PATTERN.finditer(text)
            if self._looks_like_ticker(match.group(1), allow_one_letter=False)
        ]
        return sorted(dict.fromkeys([*cashtags, *bare]))

    def _looks_like_ticker(self, value: str, *, allow_one_letter: bool) -> bool:
        normalized = value.upper()
        if normalized in self.blocked_tickers:
            return False
        if (
            len(normalized) == 1
            and not allow_one_letter
            and normalized not in self.allowed_single_letter_tickers
        ):
            return False
        if normalized.lower() in self.non_entity_terms:
            return False
        minimum_length = 1 if allow_one_letter else 2
        return bool(normalized.isalpha() and minimum_length <= len(normalized) <= 5)

    async def _extract_with_llm(
        self,
        *,
        text: str,
        source_family: str,
        trust_score: float,
        article_id: int | None,
        summary_id: int | None,
        source_id: int | None,
    ) -> list[ExtractedMention]:
        if not self.client or not self.settings:
            return []
        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract public-market mentions from the item. Return strict JSON "
                            'with {"mentions":[{"ticker":null|string,"theme":null|string,'
                            '"confidence":0-1,"evidence":"short quote"}]}. '
                            "Do not invent tickers. Use null ticker for theme-only signals."
                        ),
                    },
                    {"role": "user", "content": text[:3000]},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                timeout=self.settings.llm_timeout_seconds,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except (APIError, APITimeoutError, TimeoutError, json.JSONDecodeError, TypeError):
            return []
        raw_mentions = payload.get("mentions") if isinstance(payload, dict) else None
        if not isinstance(raw_mentions, list):
            return []
        mentions: list[ExtractedMention] = []
        for item in raw_mentions[:5]:
            if not isinstance(item, dict):
                continue
            confidence = _coerce_confidence(item.get("confidence"))
            if confidence < 0.7:
                continue
            ticker = _normalize_related_ticker(str(item.get("ticker") or ""))
            if ticker in self.blocked_tickers:
                ticker = None
            theme = str(item.get("theme") or "").strip() or None
            if not ticker and not theme:
                continue
            mentions.append(
                ExtractedMention(
                    ticker=ticker,
                    theme=theme,
                    mention_count=1,
                    evidence_text=str(item.get("evidence") or text[:220]),
                    source_family=source_family,
                    trust_score=trust_score,
                    article_id=article_id,
                    summary_id=summary_id,
                    source_id=source_id,
                )
            )
        return mentions


def extract_themes(
    text: str,
    theme_keywords: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    lowered = text.lower()
    keyword_map = theme_keywords or DEFAULT_THEME_KEYWORDS
    themes = [
        theme
        for theme, keywords in keyword_map.items()
        if any(_keyword_matches(lowered, keyword) for keyword in keywords)
    ]
    return themes


def extract_tickers(text: str) -> list[str]:
    cashtags = [
        match.group(1).upper()
        for match in CASHTAG_PATTERN.finditer(text)
        if _looks_like_ticker(match.group(1), allow_one_letter=True)
    ]
    bare = [
        match.group(1).upper()
        for match in BARE_UPPERCASE_TICKER_PATTERN.finditer(text)
        if _looks_like_ticker(match.group(1), allow_one_letter=False)
    ]
    return sorted(dict.fromkeys([*cashtags, *bare]))


def _ticker_count(text: str, ticker: str | None) -> int:
    if ticker is None:
        return 0
    return sum(1 for token in TOKEN_PATTERN.findall(text.upper()) if token.strip("$") == ticker)


def _looks_like_ticker(value: str, *, allow_one_letter: bool) -> bool:
    normalized = value.upper()
    minimum_length = 1 if allow_one_letter else 2
    return bool(
        normalized.isalpha()
        and minimum_length <= len(normalized) <= 5
        and normalized not in DEFAULT_NON_ENTITY_TICKERS
    )


def _normalize_related_ticker(value: str) -> str | None:
    normalized = value.upper().strip().lstrip("$")
    if not normalized.isalpha() or len(normalized) > 5:
        return None
    return normalized


def _keyword_matches(lowered_text: str, keyword: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)", lowered_text))


def _theme_keywords_from_settings(settings: Settings | None) -> dict[str, tuple[str, ...]]:
    if settings is None:
        return DEFAULT_THEME_KEYWORDS
    try:
        payload = json.loads(settings.market_research_theme_config)
    except json.JSONDecodeError:
        return DEFAULT_THEME_KEYWORDS
    if not isinstance(payload, dict):
        return DEFAULT_THEME_KEYWORDS
    parsed: dict[str, tuple[str, ...]] = {}
    for theme, keywords in payload.items():
        if not isinstance(theme, str):
            continue
        if not isinstance(keywords, list | tuple):
            continue
        values = tuple(str(item).strip().lower() for item in keywords if str(item).strip())
        if values:
            parsed[theme] = values
    return parsed or DEFAULT_THEME_KEYWORDS


def _csv_set(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def _coerce_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _evidence_snippet(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
