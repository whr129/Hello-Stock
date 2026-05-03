from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    message_to_dict,
    messages_from_dict,
)
from langgraph.graph.message import add_messages


def append_message(
    state: dict[str, Any],
    role: str,
    content: str,
    max_messages: int = 20,
) -> dict[str, Any]:
    current = list(state.get("messages", []))
    payload = _message_for_role(role, content)
    state["messages"] = trim_messages(add_messages(current, [payload]), max_messages=max_messages)
    return state


def deserialize_state(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw_messages = [_normalize_message(item) for item in (payload or {}).get("messages", [])]
    if not raw_messages:
        return {"messages": []}
    return {
        "messages": trim_messages(
            messages_from_dict(raw_messages),
            max_messages=len(raw_messages),
        )
    }


def serialize_state(state: dict[str, Any], *, max_messages: int = 20) -> dict[str, Any]:
    messages = trim_messages(list(state.get("messages", [])), max_messages=max_messages)
    return {"messages": [message_to_dict(message) for message in messages]}


def render_messages(messages: list[AnyMessage], *, limit: int = 8) -> list[str]:
    rendered: list[str] = []
    for item in messages[-limit:]:
        role = "assistant" if getattr(item, "type", "") == "ai" else "user"
        rendered.append(f"- {role}: {item.content}")
    return rendered


def trim_messages(messages: list[AnyMessage], *, max_messages: int) -> list[AnyMessage]:
    return list(messages[-max_messages:])


def expiry(minutes: int = 60) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


def _message_for_role(role: str, content: str) -> AnyMessage:
    metadata = {"at": datetime.now(UTC).isoformat()}
    if role == "assistant":
        return AIMessage(content=content, additional_kwargs=metadata)
    return HumanMessage(content=content, additional_kwargs=metadata)


def _normalize_message(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"type": "human", "data": {"content": str(item)}}
    if "type" in item and "data" in item:
        return item

    role = item.get("role")
    if role in {"user", "assistant"}:
        message_type = "ai" if role == "assistant" else "human"
        additional_kwargs = {}
        if item.get("at"):
            additional_kwargs["at"] = item["at"]
        return {
            "type": message_type,
            "data": {
                "content": item.get("content", ""),
                "additional_kwargs": additional_kwargs,
            },
        }

    return {"type": "human", "data": {"content": item.get("content", "")}}
