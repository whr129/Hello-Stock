from dataclasses import dataclass

from openai import APIError, AsyncOpenAI

from news_agent.settings import Settings


@dataclass(frozen=True)
class SummaryRequest:
    title: str
    text: str
    source: str


class Summarizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

    async def summarize_article(self, request: SummaryRequest) -> str:
        if self.client:
            try:
                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Summarize news in 1-2 factual sentences. Cite the source. "
                                "For market news, do not give financial advice."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Title: {request.title}\n"
                                f"Source: {request.source}\n"
                                f"Article text:\n{request.text[:4000]}"
                            ),
                        },
                    ],
                    temperature=0.2,
                )
                content = response.choices[0].message.content
                if content:
                    return content.strip()
            except APIError:
                pass

        text = request.text.strip()
        excerpt = text[:280] + ("..." if len(text) > 280 else "")
        return f"{request.title}: {excerpt} (source: {request.source})"

    async def synthesize_digest(
        self,
        headlines: list[str],
        market_lines: list[str],
        local_region: str,
    ) -> str:
        if self.client and (headlines or market_lines):
            try:
                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Create a concise digest grouped by World, Local, and Markets. "
                                "Use only provided facts. Do not provide financial advice."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Local region: {local_region}\n"
                                f"Headlines:\n{chr(10).join(headlines[:20])}\n\n"
                                f"Market context:\n{chr(10).join(market_lines[:10])}"
                            ),
                        },
                    ],
                    temperature=0.2,
                )
                content = response.choices[0].message.content
                if content:
                    return content.strip()
            except APIError:
                pass

        sections = [f"Digest for local region: {local_region}"]
        if headlines:
            sections.append("Headlines:\n" + "\n".join(f"- {line}" for line in headlines[:8]))
        if market_lines:
            sections.append("Markets:\n" + "\n".join(f"- {line}" for line in market_lines[:5]))
        return "\n\n".join(sections)
