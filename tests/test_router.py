from news_agent.agent.router import parse_message, route_request


def test_parse_command_intent() -> None:
    command, args, intent = parse_message("/research AAPL tsla")

    assert command == "/research"
    assert args == ["AAPL", "tsla"]
    assert intent == "research"


def test_parse_broad_stock_market_question_defers_to_router() -> None:
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


def test_parse_runtime_command() -> None:
    command, args, intent = parse_message("/runtime")

    assert command == "/runtime"
    assert args == []
    assert intent == "runtime"


def test_parse_research_commands() -> None:
    assert parse_message("/research")[2] == "research"
    assert parse_message("/candidates")[2] == "candidates"
    assert parse_message("/signals MU")[2] == "signals"
    assert parse_message("/researchstatus")[2] == "researchstatus"


def test_route_research_to_research_agent_for_market_news() -> None:
    route = route_request("research", args=["NVDA"])

    assert route.agents == ("research",)
    assert route.capabilities == ("market_research",)


def test_removed_commands_route_unknown() -> None:
    command, args, intent = parse_message("/brief")

    assert command == "/brief"
    assert args == []
    assert intent == "unknown"


def test_route_general_chat_to_general_search() -> None:
    route = route_request("general_chat", message_text="hello there")

    assert route.agents == ()
    assert route.capabilities == ("general_search",)
    assert route.fallback_response is None


def test_route_skills_to_news_skills_capability() -> None:
    route = route_request("skills")

    assert route.agents == ("news",)
    assert route.capabilities == ("skills",)


def test_route_runtime_to_runtime_agent() -> None:
    route = route_request("runtime")

    assert route.agents == ("runtime",)
    assert route.capabilities == ("runtime_inspection",)


def test_route_research_to_research_agent() -> None:
    route = route_request("candidates")

    assert route.agents == ("research",)
    assert route.capabilities == ("market_research",)


def test_route_runtime_like_general_chat_to_runtime_agent() -> None:
    route = route_request("general_chat", message_text="what happened in the last refresh?")

    assert route.agents == ("runtime",)
    assert route.capabilities == ("runtime_inspection",)
