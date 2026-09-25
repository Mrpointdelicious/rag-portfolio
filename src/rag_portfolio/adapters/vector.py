from qdrant_client import AsyncQdrantClient

from rag_portfolio.config import Settings


class QdrantConnection:
    def __init__(self, settings: Settings):
        self.client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() or None,
            timeout=settings.dependency_timeout_seconds,
            check_compatibility=False,
        )

    async def check(self) -> None:
        await self.client.get_collections()

    async def close(self) -> None:
        await self.client.close()
