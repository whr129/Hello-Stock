from news_agent.agent.router import parse_message, route_intent, route_request


def test_parse_command_intent() -> None:
    command, args, intent = parse_message("/watch AAPL tsla")

    assert command == "/watch"
    assert args == ["AAPL", "tsla"]
    assert intent == "watch"


def test_parse_broad_stock_market_question_as_brief() -> None:
    _, _, intent = parse_message("what happened to the stock market today")

    assert intent == "general_chat"


def test_parse_company_stock_price_defers_to_llm_router() -> None:
    _, args, intent = parse_message("give me real time stock price for Google")

    assert args == []
    assert intent == "general_chat"


def test_extract_explicit_ticker_stock_price() -> None:
    from news_agent.agent.router import extract_stock_symbols

    args = extract_stock_symbols("give me price for GooG")

    assert args == ["GOOG"]


def test_parse_general_chat_intent() -> None:
    _, _, intent = parse_message("hello how are you")

    assert intent == "general_chat"


def test_parse_remove_source_command() -> None:
    command, args, intent = parse_message("/removesource 3")

    assert command == "/removesource"
    assert args == ["3"]
    assert intent == "removesource"


def test_parse_refresh_command() -> None:
    command, args, intent = parse_message("/refresh")

    assert command == "/refresh"
    assert args == []
    assert intent == "refresh"


def test_parse_skills_command() -> None:
    command, args, intent = parse_message("/skills")

    assert command == "/skills"
    assert args == []
    assert intent == "skills"


def test_route_brief_to_news_subagent() -> None:
    route = route_intent("brief")

    assert route.agents == ("news",)
    assert route.capabilities == ("news_brief",)


def test_route_stocks_to_stock_tool_without_news_context() -> None:
    route = route_intent("stocks")

    assert route.agents == ("market",)
    assert route.capabilities == ("market_snapshot", "technical_analysis")


def test_route_mixed_news_and_stock_request_to_both_subagents() -> None:
    route = route_request("brief", args=["NVDA"])

    assert route.agents == ("news", "market")
    assert route.capabilities == ("news_brief", "market_snapshot", "technical_analysis")


def test_route_general_chat_to_general_search() -> None:
    route = route_request("general_chat", message_text="hello there")

    assert route.agents == ()
    assert route.capabilities == ("general_search",)
    assert route.fallback_response is None


def test_route_skills_to_news_skills_capability() -> None:
    route = route_request("skills")

    assert route.agents == ("news",)
    assert route.capabilities == ("skills",)
