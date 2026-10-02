"""Small, closeable adapter for the native DashScope dense embedding endpoint."""

import math
from typing import Literal

import httpx

from rag_portfolio.config import Settings

ENDPOINT = "/services/embeddings/text-embedding/text-embedding"


class EmbeddingAdapterError(RuntimeError):
    """Provider/configuration errors that never include credentials or response bodies."""


class EmbeddingAdapter:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if settings.embedding_provider != "alibaba_dashscope":
            raise EmbeddingAdapterError("Only alibaba_dashscope is supported")
        if settings.embedding_output_type != "dense":
            raise EmbeddingAdapterError("Only dense embedding output is supported")
        if not settings.embedding_base_url.strip() or not settings.embedding_model.strip():
            raise EmbeddingAdapterError("Embedding base URL and model are required")
        if not settings.embedding_api_key.get_secret_value().strip():
            raise EmbeddingAdapterError("RAG_EMBEDDING_API_KEY is required")
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension
        self.output_type = settings.embedding_output_type
        self.timeout = settings.embedding_timeout_seconds
        self.url = settings.embedding_base_url.rstrip("/") + ENDPOINT
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {settings.embedding_api_key.get_secret_value()}"},
            timeout=self.timeout,
            transport=transport,
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "document")

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "query"))[0]

    async def _embed(
        self, texts: list[str], text_type: Literal["document", "query"]
    ) -> list[list[float]]:
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingAdapterError("Embedding inputs must contain nonempty text")
        try:
            response = await self.client.post(
                self.url,
                json={
                    "model": self.model,
                    "input": {"texts": texts},
                    "parameters": {
                        "text_type": text_type,
                        "dimension": self.dimension,
                        "output_type": self.output_type,
                    },
                },
            )
        except httpx.TimeoutException:
            raise EmbeddingAdapterError(
                f"DashScope embedding request timed out after {self.timeout:g}s"
            ) from None
        except httpx.RequestError:
            raise EmbeddingAdapterError("DashScope embedding request failed") from None
        if not response.is_success:
            raise EmbeddingAdapterError(f"DashScope embedding HTTP error: {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise EmbeddingAdapterError("DashScope returned invalid JSON") from None
        return self._parse_embeddings(body, len(texts))

    def _parse_embeddings(self, body: object, expected_count: int) -> list[list[float]]:
        output = body.get("output") if isinstance(body, dict) else None
        embeddings = output.get("embeddings") if isinstance(output, dict) else None
        if not isinstance(embeddings, list):
            raise EmbeddingAdapterError("DashScope response must contain output.embeddings")
        if len(embeddings) != expected_count:
            raise EmbeddingAdapterError(
                f"Embedding count mismatch: expected {expected_count}, got {len(embeddings)}"
            )
        ordered: dict[int, list[float]] = {}
        for item in embeddings:
            if not isinstance(item, dict):
                raise EmbeddingAdapterError("Invalid embedding response item")
            index = item.get("text_index")
            if type(index) is not int or not 0 <= index < expected_count or index in ordered:
                raise EmbeddingAdapterError("Invalid or duplicate embedding text_index")
            vector = item.get("embedding")
            if not isinstance(vector, list):
                raise EmbeddingAdapterError("Embedding vector must be an array")
            if len(vector) != self.dimension:
                raise EmbeddingAdapterError(
                    f"Embedding dimension mismatch: expected {self.dimension}, got {len(vector)}"
                )
            try:
                valid = all(
                    type(value) in (int, float) and math.isfinite(value) for value in vector
                )
            except OverflowError:
                valid = False
            if not valid:
                raise EmbeddingAdapterError("Embedding vector must contain finite numbers")
            ordered[index] = [float(value) for value in vector]
        return [ordered[index] for index in range(expected_count)]

    async def close(self) -> None:
        await self.client.aclose()
