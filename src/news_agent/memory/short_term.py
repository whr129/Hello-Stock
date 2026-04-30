from datetime import UTC, datetime, timedelta
from typing import Any


def append_message(
    state: dict[str, Any],
    role: str,
    content: str,
    max_messages: int = 12,
) -> dict[str, Any]:
    messages = list(state.get("messages", []))
    messages.append({"role": role, "content": content, "at": datetime.now(UTC).isoformat()})
    state["messages"] = messages[-max_messages:]
    return state


def expiry(minutes: int = 60) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)
