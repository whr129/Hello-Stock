from news_agent.agent.router import parse_message, route_request, skills_response


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


def test_extract_stock_symbols_rejects_common_non_tickers() -> None:
    from news_agent.agent.router import extract_stock_symbols

    args = extract_stock_symbols("Research AI CEO CPA HBM THIS and I")

    assert args == []


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


def test_parse_resources_command() -> None:
    command, args, intent = parse_message("/resources")

    assert command == "/resources"
    assert args == []
    assert intent == "resources"


def test_parse_sourcepack_command() -> None:
    command, args, intent = parse_message("/sourcepack macro")

    assert command == "/sourcepack"
    assert args == ["macro"]
    assert intent == "sourcepack"


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


def test_skills_response_is_compact_and_includes_pipeline_trigger() -> None:
    response = skills_response()

    assert "Research:" in response
    assert "Sources:" in response
    assert "Source packs: /sourcepack [category]" in response
    assert "Pipelines:" in response
    assert "/refresh [market_prices|breaking_resources|daily_resources|all]" in response
    assert "/refresh prices" in response
    assert "General web questions" not in response


def test_route_resources_to_news_inventory_capability() -> None:
    route = route_request("resources")

    assert route.agents == ("news",)
    assert route.capabilities == ("resource_inventory",)


def test_route_sourcepack_to_news_source_admin_capability() -> None:
    route = route_request("sourcepack")

    assert route.agents == ("news",)
    assert route.capabilities == ("source_admin",)


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


def test_route_latest_failed_refresh_to_runtime_agent() -> None:
    route = route_request(
        "general_chat",
        message_text="What failed in the latest market research refresh?",
    )

    assert route.agents == ("runtime",)
