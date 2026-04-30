from news_agent.agent.router import parse_message


def test_parse_command_intent() -> None:
    command, args, intent = parse_message("/watch AAPL tsla")

    assert command == "/watch"
    assert args == ["AAPL", "tsla"]
    assert intent == "watch"


def test_parse_natural_language_stock_intent() -> None:
    _, _, intent = parse_message("what happened to the stock market today")

    assert intent == "stocks"


def test_parse_general_chat_intent() -> None:
    _, _, intent = parse_message("hello how are you")

    assert intent == "general_chat"


def test_parse_remove_source_command() -> None:
    command, args, intent = parse_message("/removesource 3")

    assert command == "/removesource"
    assert args == ["3"]
    assert intent == "removesource"
