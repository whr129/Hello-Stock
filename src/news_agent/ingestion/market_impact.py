from __future__ import annotations

import json
import re
from dataclasses import dataclass

from openai import APIError, APITimeoutError, AsyncOpenAI

from news_agent.settings import Settings


@dataclass(frozen=True)
class MarketImpactDecision:
    accepted: bool
    confidence: float
    reason: str
    method: str
    matched_terms: tuple[str, ...] = ()
    matched_category: str | None = None

    def metadata(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "accepted": self.accepted,
            "confidence": self.confidence,
            "reason": self.reason,
            "method": self.method,
        }
        if self.matched_terms:
            payload["matched_terms"] = list(self.matched_terms)
        if self.matched_category:
            payload["matched_category"] = self.matched_category
        return payload


class MarketImpactClassifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.allowed_categories = _parse_csv(settings.market_impact_allowed_categories)
        self.keywords = _parse_csv(settings.market_impact_keywords)
        self.reject_terms = tuple(_parse_csv(settings.market_impact_reject_terms))
        self.minimum_confidence = settings.market_impact_minimum_confidence
        self.llm_threshold = settings.llm_market_impact_classification_threshold
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key)
            if settings.llm_market_impact_classification_enabled and settings.openai_api_key
            else None
        )

    async def classify(
        self,
        *,
        title: str,
        text: str,
        category: str,
        source: str = "",
        provider: str = "",
    ) -> MarketImpactDecision:
        deterministic = self.classify_deterministic(
            title=title,
            text=text,
            category=category,
        )
        if deterministic.confidence >= self.minimum_confidence:
            return deterministic
        if deterministic.method == "deterministic_reject":
            return deterministic
        if not self.client or deterministic.confidence > self.llm_threshold:
            return deterministic
        return await self._classify_with_llm(
            title=title,
            text=text,
            category=category,
            source=source,
            provider=provider,
            fallback=deterministic,
        )

    def classify_deterministic(
        self,
        *,
        title: str,
        text: str,
        category: str,
    ) -> MarketImpactDecision:
        normalized_category = (category or "").strip().lower()
        haystack = f"{title} {text}".lower()
        reject_terms = _matched_terms(haystack, self.reject_terms)
        if reject_terms:
            return MarketImpactDecision(
                accepted=False,
                confidence=0.95,
                reason="obvious non-market content matched",
                method="deterministic_reject",
                matched_terms=tuple(reject_terms),
            )

        if normalized_category in self.allowed_categories:
            return MarketImpactDecision(
                accepted=True,
                confidence=0.95,
                reason="source category is configured as market-impact relevant",
                method="deterministic_category",
                matched_category=normalized_category,
            )

        market_terms = _matched_terms(haystack, self.keywords)

        if len(market_terms) >= 2:
            return MarketImpactDecision(
                accepted=True,
                confidence=0.9,
                reason="multiple configured market-impact terms matched",
                method="deterministic_terms",
                matched_terms=tuple(market_terms),
            )
        if len(market_terms) == 1:
            return MarketImpactDecision(
                accepted=True,
                confidence=0.72,
                reason="one configured market-impact term matched",
                method="deterministic_terms",
                matched_terms=tuple(market_terms),
            )
        return MarketImpactDecision(
            accepted=False,
            confidence=0.5,
            reason="no configured market-impact signal matched",
            method="deterministic_uncertain",
        )

    async def _classify_with_llm(
        self,
        *,
        title: str,
        text: str,
        category: str,
        source: str,
        provider: str,
        fallback: MarketImpactDecision,
    ) -> MarketImpactDecision:
        if not self.client:
            return fallback
        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Classify whether an item is likely to affect public stocks, "
                            "equity sectors, rates, macro expectations, regulation, "
                            "earnings, filings, M&A, sanctions, tariffs, or market liquidity. "
                            "Return strict JSON with accepted, confidence, and reason."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Title: {title}\n"
                            f"Category: {category}\n"
                            f"Source: {source}\n"
                            f"Provider: {provider}\n"
                            f"Text:\n{text[:3000]}"
                        ),
                    },
                ],
                temperature=0,
                timeout=self.settings.llm_timeout_seconds,
            )
        except (APIError, APITimeoutError, TimeoutError):
            return MarketImpactDecision(
                accepted=False,
                confidence=fallback.confidence,
                reason="LLM classification failed; rejected by deterministic fallback",
                method="llm_fallback",
                matched_terms=fallback.matched_terms,
                matched_category=fallback.matched_category,
            )

        content = response.choices[0].message.content or ""
        payload = _load_json_object(content)
        if payload is None:
            return fallback
        confidence = _coerce_confidence(payload.get("confidence"))
        accepted = bool(payload.get("accepted")) and confidence >= self.minimum_confidence
        reason = str(payload.get("reason") or "LLM market-impact classification")
        return MarketImpactDecision(
            accepted=accepted,
            confidence=confidence,
            reason=reason,
            method="llm",
        )


DEFAULT_ALLOWED_CATEGORIES = (
    "tech,macro,policy,regulatory,earnings,filings,finance,markets"
)
DEFAULT_MARKET_IMPACT_KEYWORDS = (
    "AI,semiconductor,semiconductors,rates,CPI,tariff,tariffs,sanction,sanctions,"
    "earnings,M&A,guidance,capex,"
    "regulation,acquisition,merger,antitrust,bank,bond,company,economy,export control,"
    "fed,filing,forecast,inflation,IPO,market,nasdaq,profit,revenue,sales,shares,stock,"
    "treasury"
)
def _parse_csv(value: str) -> frozenset[str]:
    return frozenset(item.strip().lower() for item in value.split(",") if item.strip())


def _matched_terms(haystack: str, terms: frozenset[str] | tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for term in terms:
        normalized = term.lower()
        if re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", haystack):
            matches.append(normalized)
    return sorted(matches)


def _load_json_object(content: str) -> dict | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _coerce_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))
