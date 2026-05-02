from news_agent.memory.short_term import append_message, deserialize_state, serialize_state


def test_append_message_keeps_recent_window() -> None:
    state = {}

    for index in range(5):
        append_message(state, "user", f"message {index}", max_messages=3)

    assert [item.content for item in state["messages"]] == [
        "message 2",
        "message 3",
        "message 4",
    ]


def test_serialize_round_trip_preserves_messages() -> None:
    state = {}
    append_message(state, "user", "hello", max_messages=5)
    append_message(state, "assistant", "hi", max_messages=5)

    payload = serialize_state(state, max_messages=5)
    restored = deserialize_state(payload)

    assert [item.content for item in restored["messages"]] == ["hello", "hi"]
