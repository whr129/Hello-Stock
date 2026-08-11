from news_agent.agent.intent import ROUTER_SYSTEM_PROMPT
from news_agent.agent.reflection import REFLECTION_PROMPT
from news_agent.evaluation.runner import JUDGE_PROMPT
from news_agent.ingestion.market_impact import MARKET_IMPACT_PROMPT
from news_agent.memory.consolidation import (
    CONSOLIDATION_PROMPT,
    EXTRACTION_PROMPT,
    TURN_EXTRACTION_PROMPT,
)
from news_agent.research.extraction import MENTION_EXTRACTION_PROMPT
from news_agent.search.service import GENERAL_SEARCH_PROMPT
from news_agent.summarizer.service import ARTICLE_SUMMARY_PROMPT, Summarizer

ACTIVE_PROMPTS = (
    ROUTER_SYSTEM_PROMPT,
    REFLECTION_PROMPT,
    GENERAL_SEARCH_PROMPT,
    ARTICLE_SUMMARY_PROMPT,
    MARKET_IMPACT_PROMPT,
    MENTION_EXTRACTION_PROMPT,
    EXTRACTION_PROMPT,
    TURN_EXTRACTION_PROMPT,
    CONSOLIDATION_PROMPT,
    JUDGE_PROMPT,
)


def test_every_active_prompt_marks_supplied_content_as_untrusted() -> None:
    assert len(ACTIVE_PROMPTS) == 10
    assert all("untrusted" in prompt.lower() for prompt in ACTIVE_PROMPTS)


def test_removed_digest_prompt_has_no_runtime_surface() -> None:
    assert not hasattr(Summarizer, "synthesize_digest")
