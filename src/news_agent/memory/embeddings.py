from collections.abc import Sequence

from openai import APIError, AsyncOpenAI

from news_agent.settings import Settings


def zero_embedding(dimensions: int = 1536) -> list[float]:
    return [0.0] * dimensions


def cosine_safe_query_vector(values: Sequence[float] | None, dimensions: int = 1536) -> list[float]:
    if not values:
        return zero_embedding(dimensions)
    padded = list(values[:dimensions])
    return padded + [0.0] * (dimensions - len(padded))


class EmbeddingService:
    def __init__(self, settings: Settings, dimensions: int = 1536) -> None:
        self.settings = settings
        self.dimensions = dimensions
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

    async def embed_text(self, text: str) -> list[float]:
        if not text.strip() or self.client is None:
            return zero_embedding(self.dimensions)

        try:
            response = await self.client.embeddings.create(
                model=self.settings.embedding_model,
                input=text[:8000],
            )
            return cosine_safe_query_vector(response.data[0].embedding, self.dimensions)
        except APIError:
            return zero_embedding(self.dimensions)
