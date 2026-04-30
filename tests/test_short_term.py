from news_agent.memory.short_term import append_message


def test_append_message_keeps_recent_window() -> None:
    state = {}

    for index in range(5):
        append_message(state, "user", f"message {index}", max_messages=3)

    assert [item["content"] for item in state["messages"]] == [
        "message 2",
        "message 3",
        "message 4",
    ]
