from dataclasses import dataclass
from typing import Any

from openai import APIError, AsyncOpenAI

from news_agent.settings import Settings


@dataclass(frozen=True)
class ReActResult:
    answer: str
    action: str
    observation: str


class ReActResponder:
    """Small ReAct-style responder that keeps reasoning private and returns only the answer."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

    async def respond(self, message: str, context: dict[str, Any]) -> ReActResult:
        action = self._choose_action(message)
        observation = self._observe(action, context)

        if self.client:
            try:
                answer = await self._llm_answer(message, action, observation, context)
                if answer:
                    return ReActResult(answer=answer, action=action, observation=observation)
            except APIError:
                pass

        return ReActResult(
            answer=self._fallback_answer(message, action, observation, context),
            action=action,
            observation=observation,
        )

    def _choose_action(self, message: str) -> str:
        lowered = message.lower()
        if any(token in lowered for token in ("help", "what can you do", "commands")):
            return "explain_capabilities"
        if any(token in lowered for token in ("remember", "prefer", "my local", "block")):
            return "memory_note"
        if any(token in lowered for token in ("hello", "hi", "hey")):
            return "greet"
        return "general_answer"

    def _observe(self, action: str, context: dict[str, Any]) -> str:
        if action == "explain_capabilities":
            return (
                "The bot supports market research, stock context, source config, "
                "runtime inspection, and memory."
            )
        if action == "memory_note":
            return "The message may contain a preference that the memory node can store."
        if action == "greet":
            return "The user is opening a casual conversation."

        article_count = len(context.get("articles", []))
        return f"Available context: {article_count} market-impact articles."

    async def _llm_answer(
        self,
        message: str,
        action: str,
        observation: str,
        context: dict[str, Any],
    ) -> str | None:
        memories = "\n".join(f"- {item}" for item in context.get("memories", [])[:8])
        recent_messages = "\n".join(
            f"- {item.get('role')}: {item.get('content')}"
            for item in context.get("recent_messages", [])[-8:]
        )
        articles = "\n".join(
            f"- {article.get('title', 'Untitled')}" for article in context.get("articles", [])[:5]
        )
        response = await self.client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise Telegram market research assistant. "
                        "Use ReAct internally, but never reveal hidden reasoning, "
                        "or labels like Thought/Action/Observation. If discussing stocks, avoid "
                        "buy/sell recommendations and keep it informational."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User message: {message}\n"
                        f"Chosen action: {action}\n"
                        f"Observation: {observation}\n"
                        f"Recent conversation:\n{recent_messages or '- none'}\n"
                        f"Relevant memories:\n{memories or '- none'}\n"
                        f"Recent articles:\n{articles or '- none'}"
                    ),
                },
            ],
            temperature=0.4,
        )
        return response.choices[0].message.content

    def _fallback_answer(
        self,
        message: str,
        action: str,
        observation: str,
        context: dict[str, Any],
    ) -> str:
        if action == "greet":
            return (
                "Hi. I can help with market-impact research, stock context, source management, "
                "runtime inspection, and memory. Try /research, /candidates, /signals MU, "
                "/stocks AAPL, or /sources."
            )
        if action == "explain_capabilities":
            return (
                "I can collect market-impact sources, rank emerging ticker signals, explain "
                "candidate evidence, show stock context, inspect runtime history, and "
                "manage memory. "
                "Useful commands: /research, /candidates, /signals, /stocks, /sources, /memory."
            )
        if action == "memory_note":
            return "Got it. I will treat that as a preference where appropriate."

        return (
            "I’m here. Ask for market research, candidate signals, stock context, "
            "or source management."
        )
