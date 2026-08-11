from news_agent.settings import get_settings


def enforce_financial_guardrails(text: str) -> str:
    settings = get_settings()
    financial_advice_terms = [
        term.strip().lower() for term in settings.financial_advice_terms.split(",") if term.strip()
    ]
    lowered = text.lower()
    if any(term in lowered for term in financial_advice_terms):
        return (
            "I cannot provide buy/sell recommendations. I can summarize facts, price movement, "
            "news context, and technical indicators for informational use."
        )

    disclaimer = settings.financial_guardrail_disclaimer
    if "financial advice" not in lowered:
        return f"{text}\n\n{disclaimer}"
    return text


def has_source_attribution(text: str) -> bool:
    lowered = text.lower()
    return "source:" in lowered or "sources:" in lowered or "via " in lowered
