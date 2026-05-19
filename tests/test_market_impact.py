from types import SimpleNamespace

import pytest

from news_agent.ingestion.market_impact import MarketImpactClassifier
from news_agent.settings import Settings


def test_deterministic_classifier_accepts_market_impact_categories_and_terms() -> None:
    classifier = MarketImpactClassifier(Settings(openai_api_key=""))

    earnings = classifier.classify_deterministic(
        title="Company raises guidance after earnings beat",
        text="Revenue and profit exceeded estimates.",
        category="earnings",
    )
    macro = classifier.classify_deterministic(
        title="Fed signals rates may stay high after CPI report",
        text="Treasury yields moved after the policy remarks.",
        category="general",
    )
    policy = classifier.classify_deterministic(
        title="White House expands semiconductor export controls",
        text="New regulation affects AI chip sales.",
        category="policy",
    )

    assert earnings.accepted is True
    assert macro.accepted is True
    assert policy.accepted is True


def test_deterministic_classifier_rejects_obvious_non_market_content() -> None:
    classifier = MarketImpactClassifier(Settings(openai_api_key=""))

    decision = classifier.classify_deterministic(
        title="Local sports tournament starts this weekend",
        text="The city tournament schedule was released.",
        category="general",
    )

    assert decision.accepted is False
    assert decision.method == "deterministic_reject"


def test_deterministic_classifier_rejects_personal_finance_before_category_acceptance() -> None:
    classifier = MarketImpactClassifier(Settings(openai_api_key=""))

    decision = classifier.classify_deterministic(
        title="I inherited a house. My CPA says I should sell within a year.",
        text="The plan is to sell it to another family member for the appraised value.",
        category="markets",
    )

    assert decision.accepted is False
    assert decision.method == "deterministic_reject"


@pytest.mark.asyncio
async def test_llm_classification_accepts_uncertain_market_impact() -> None:
    classifier = MarketImpactClassifier(
        Settings(
            openai_api_key="test",
            llm_market_impact_classification_enabled=True,
        )
    )
    classifier.client = _FakeClient(
        '{"accepted": true, "confidence": 0.91, "reason": "supplier risk"}'
    )

    decision = await classifier.classify(
        title="Factory shutdown affects a major supplier",
        text="The supplier disruption could affect several listed manufacturers.",
        category="general",
    )

    assert decision.accepted is True
    assert decision.method == "llm"


@pytest.mark.asyncio
async def test_llm_classification_rejects_uncertain_non_market_item() -> None:
    classifier = MarketImpactClassifier(
        Settings(
            openai_api_key="test",
            llm_market_impact_classification_enabled=True,
        )
    )
    classifier.client = _FakeClient(
        '{"accepted": false, "confidence": 0.88, "reason": "no stock relevance"}'
    )

    decision = await classifier.classify(
        title="Museum announces a new weekend exhibit",
        text="The exhibit opens Saturday.",
        category="general",
    )

    assert decision.accepted is False
    assert decision.method == "llm"


@pytest.mark.asyncio
async def test_llm_classification_failure_rejects_uncertain_item() -> None:
    classifier = MarketImpactClassifier(
        Settings(
            openai_api_key="test",
            llm_market_impact_classification_enabled=True,
        )
    )
    classifier.client = _FakeClient(error=TimeoutError("timeout"))

    decision = await classifier.classify(
        title="Supplier update has unclear public company impact",
        text="Details were sparse.",
        category="general",
    )

    assert decision.accepted is False
    assert decision.method == "llm_fallback"


class _FakeClient:
    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.content = content
        self.error = error

    async def _create(self, **kwargs):
        del kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )
