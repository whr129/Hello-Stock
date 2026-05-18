from news_agent.agent.tools import _requested_tickers


def test_requested_tickers_does_not_use_removed_watchlist_context() -> None:
    tickers = _requested_tickers(
        {
            "command": "",
            "args": ["what", "happened", "to", "the", "stock", "market", "today"],
            "message_text": "what happened to the stock market today",
            "watched_tickers": ["AAPL", "TSLA"],
        }
    )

    assert tickers == []


def test_requested_tickers_allows_cashtags_and_uppercase_mentions() -> None:
    tickers = _requested_tickers(
        {
            "command": "",
            "args": ["compare", "$nvda", "and", "MSFT"],
            "message_text": "compare $nvda and MSFT",
            "watched_tickers": ["AAPL"],
        }
    )

    assert tickers == ["MSFT", "NVDA"]


def test_requested_tickers_uses_stocks_command_args() -> None:
    tickers = _requested_tickers(
        {
            "command": "/stocks",
            "args": ["aapl", "tsla"],
            "message_text": "/stocks aapl tsla",
            "watched_tickers": ["NVDA"],
        }
    )

    assert tickers == ["AAPL", "TSLA"]


def test_requested_tickers_uses_router_extracted_natural_language_args() -> None:
    tickers = _requested_tickers(
        {
            "intent": "stocks",
            "command": "",
            "args": ["GOOGL"],
            "message_text": "give me price for Google",
            "watched_tickers": ["AAPL", "TSLA"],
        }
    )

    assert tickers == ["GOOGL"]
