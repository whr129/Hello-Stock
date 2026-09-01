from telegram.ext import MessageHandler, filters

from news_agent.bot.handlers import (
    TELEGRAM_SAFE_MESSAGE_LIMIT,
    register_handlers,
    split_telegram_message,
)


def test_split_telegram_message_keeps_short_message_intact() -> None:
    assert split_telegram_message("short response") == ["short response"]


def test_split_telegram_message_chunks_long_message_under_safe_limit() -> None:
    text = "\n\n".join(f"Section {index}\n" + ("x" * 300) for index in range(30))

    chunks = split_telegram_message(text)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= TELEGRAM_SAFE_MESSAGE_LIMIT for chunk in chunks)
    assert "".join("".join(chunks).split()) == "".join(text.split())


def test_register_handlers_catches_unknown_commands() -> None:
    class FakeApplication:
        def __init__(self) -> None:
            self.handlers = []

        def add_handler(self, handler) -> None:
            self.handlers.append(handler)

    application = FakeApplication()

    register_handlers(application)

    catch_all = application.handlers[-1]
    assert isinstance(catch_all, MessageHandler)
    assert catch_all.filters is filters.COMMAND
