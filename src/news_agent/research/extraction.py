import re
from collections.abc import Iterable

from news_agent.agent.router import extract_stock_symbols
from news_agent.research.schemas import ExtractedMention

THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
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
NON_ENTITY_TICKERS = {"AI", "CEO", "CFO", "ETF", "HBM", "IPO", "SEC", "USA"}


class MentionExtractor:
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
            *extract_stock_symbols(clean_text),
            *(ticker.upper() for ticker in related_tickers),
        ]
        tickers = sorted(
            dict.fromkeys(ticker for ticker in extracted if ticker not in NON_ENTITY_TICKERS)
        )
        themes = extract_themes(clean_text)
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


def extract_themes(text: str) -> list[str]:
    lowered = text.lower()
    themes = [
        theme
        for theme, keywords in THEME_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    ]
    return themes


def _ticker_count(text: str, ticker: str | None) -> int:
    if ticker is None:
        return 0
    return sum(1 for token in TOKEN_PATTERN.findall(text.upper()) if token.strip("$") == ticker)


def _evidence_snippet(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
