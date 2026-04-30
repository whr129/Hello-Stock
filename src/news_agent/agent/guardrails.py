FINANCIAL_ADVICE_TERMS = (
    "you should buy",
    "you should sell",
    "guaranteed",
    "risk-free",
    "sure profit",
)


def enforce_financial_guardrails(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in FINANCIAL_ADVICE_TERMS):
        return (
            "I cannot provide buy/sell recommendations. I can summarize facts, price movement, "
            "news context, and technical indicators for informational use."
        )

    disclaimer = "This is informational only, not financial advice."
    if "financial advice" not in lowered:
        return f"{text}\n\n{disclaimer}"
    return text


def has_source_attribution(text: str) -> bool:
    lowered = text.lower()
    return "source:" in lowered or "sources:" in lowered or "via " in lowered
