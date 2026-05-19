from news_agent.research.extraction import MentionExtractor, extract_themes, extract_tickers
from news_agent.settings import Settings


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


def test_theme_extraction_does_not_match_ai_inside_words() -> None:
    assert "AI infrastructure" not in extract_themes(
        "A CPA said the family should sell an inherited house."
    )


def test_research_ticker_extraction_ignores_chat_direct_words() -> None:
    text = (
        "I inherited a house. My CPA says I should sell within a year. "
        "The oil market is reaching a tipping point according to this strategist."
    )

    assert extract_tickers(text) == []


def test_research_ticker_extraction_allows_explicit_one_letter_cashtags() -> None:
    assert extract_tickers("$V volume rises while payments expand.") == ["V"]


def test_theme_extraction_uses_configured_theme_map() -> None:
    extractor = MentionExtractor(
        Settings(
            openai_api_key="",
            market_research_theme_config='{"custom theme":["bespoke catalyst"]}',
        )
    )

    mentions = extractor.extract(text="Bespoke catalyst appears in supplier checks.")

    assert {mention.theme for mention in mentions} == {"custom theme"}


async def test_llm_mention_extraction_runs_when_deterministic_has_no_signal() -> None:
    extractor = MentionExtractor(
        Settings(
            openai_api_key="test",
            llm_mention_extraction_enabled=True,
        )
    )
    extractor.client = _FakeClient(
        '{"mentions":[{"ticker":"NVDA","theme":"AI infrastructure",'
        '"confidence":0.9,"evidence":"supplier demand"}]}'
    )

    mentions = await extractor.extract_async(text="Supplier demand accelerated.")

    assert [(mention.ticker, mention.theme) for mention in mentions] == [
        ("NVDA", "AI infrastructure")
    ]


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content

    async def create(self, **kwargs):
        del kwargs
        return type(
            "Response",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {"message": type("Message", (), {"content": self.content})()},
                    )()
                ]
            },
        )()
