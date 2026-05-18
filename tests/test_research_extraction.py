from news_agent.research.extraction import MentionExtractor, extract_themes


def test_extracts_cashtag_ticker_and_theme() -> None:
    mentions = MentionExtractor().extract(
        text="$MU rallies as HBM memory chip demand grows for AI data center builds.",
        source_family="market news",
        trust_score=0.8,
    )

    assert mentions[0].ticker == "MU"
    assert mentions[0].theme == "AI infrastructure"
    assert mentions[0].trust_score == 0.8


def test_extracts_related_tickers_from_article_metadata() -> None:
    mentions = MentionExtractor().extract(
        text="Cloud capex remains a focus.",
        related_tickers=["msft"],
    )

    assert {mention.ticker for mention in mentions} == {"MSFT"}


def test_extract_themes_uses_keyword_map() -> None:
    assert "rates" in extract_themes("CPI and Treasury yield pressure returned.")
