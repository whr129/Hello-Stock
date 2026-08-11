from news_agent.bot.handlers import TELEGRAM_SAFE_MESSAGE_LIMIT, split_telegram_message


def test_split_telegram_message_keeps_short_message_intact() -> None:
    assert split_telegram_message("short response") == ["short response"]


def test_split_telegram_message_chunks_long_message_under_safe_limit() -> None:
    text = "\n\n".join(f"Section {index}\n" + ("x" * 300) for index in range(30))

    chunks = split_telegram_message(text)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= TELEGRAM_SAFE_MESSAGE_LIMIT for chunk in chunks)
    assert "".join("".join(chunks).split()) == "".join(text.split())
