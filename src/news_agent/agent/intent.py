from openai import APIError, AsyncOpenAI

from news_agent.agent.router import parse_message
from news_agent.graph.state import Intent
from news_agent.settings import Settings

ROUTABLE_INTENTS: set[Intent] = {"brief", "stocks", "general_chat", "help"}


class IntentClassifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

    async def classify(self, text: str) -> tuple[str, list[str], Intent]:
        command, args, intent = parse_message(text)
        if command or intent != "general_chat" or self.client is None:
            return command, args, intent

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Classify the user's message for a Telegram news bot. "
                            "Return exactly one label: brief, stocks, general_chat, or help."
                        ),
                    },
                    {"role": "user", "content": text[:1000]},
                ],
                temperature=0,
            )
        except APIError:
            return command, args, intent

        label = (response.choices[0].message.content or "").strip().lower()
        return command, args, label if label in ROUTABLE_INTENTS else intent
