from news_agent.agent.guardrails import enforce_financial_guardrails


def test_guardrail_blocks_buy_sell_advice() -> None:
    response = enforce_financial_guardrails("You should buy TSLA now.")

    assert "cannot provide buy/sell recommendations" in response


def test_guardrail_adds_disclaimer() -> None:
    response = enforce_financial_guardrails("TSLA rose today after earnings.")

    assert "not financial advice" in response
