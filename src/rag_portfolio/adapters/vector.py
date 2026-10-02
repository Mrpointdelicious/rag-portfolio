from qdrant_client import AsyncQdrantClient, models

from rag_portfolio.config import Settings


class VectorAdapterError(RuntimeError):
    """The collection cannot safely hold this embedding revision."""


def payload_filter(values: dict[str, str]) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in values.items()
        ]
    )


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

    async def recreate_collection(
        self, collection_name: str, *, dimension: int, embedding_revision: str
    ) -> None:
        # Only the smoke CLI opts into deletion, against its fixed experimental collection.
        if await self.client.collection_exists(collection_name):
            await self.client.delete_collection(collection_name)
        await self.ensure_collection(
            collection_name, dimension=dimension, embedding_revision=embedding_revision
        )

    async def ensure_collection(
        self, collection_name: str, *, dimension: int, embedding_revision: str
    ) -> None:
        if not embedding_revision:
            raise VectorAdapterError("Embedding revision is required")
        if not await self.client.collection_exists(collection_name):
            await self.client.create_collection(
                collection_name,
                vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
            )
        info = await self.client.get_collection(collection_name)
        vectors = info.config.params.vectors
        if (
            not isinstance(vectors, models.VectorParams)
            or vectors.size != dimension
            or vectors.distance != models.Distance.COSINE
        ):
            raise VectorAdapterError("Collection vector configuration differs; use --recreate")
        # Qdrant 1.15 has no collection metadata field. Check every existing point,
        # including legacy points missing a revision, before permitting any write.
        total = await self.count(collection_name)
        matching = await self.count(
            collection_name, query_filter=payload_filter({"embedding_revision": embedding_revision})
        )
        if matching != total:
            raise VectorAdapterError(
                "Collection contains a different/missing revision; use --recreate"
            )

    async def upsert(
        self,
        collection_name: str,
        points: list[models.PointStruct],
        *,
        dimension: int,
        embedding_revision: str,
    ) -> None:
        if not points:
            raise VectorAdapterError("Cannot upsert empty points")
        for point in points:
            payload = point.payload or {}
            if (
                payload.get("embedding_revision") != embedding_revision
                or payload.get("chunk_id") != str(point.id)
                or not isinstance(point.vector, list)
                or len(point.vector) != dimension
            ):
                raise VectorAdapterError("Point ID, embedding revision or dimension mismatch")
        await self.ensure_collection(
            collection_name, dimension=dimension, embedding_revision=embedding_revision
        )
        await self.client.upsert(collection_name, points=points, wait=True)

    async def search(
        self,
        collection_name: str,
        vector: list[float],
        *,
        query_filter: models.Filter,
        top_k: int = 5,
    ) -> list[models.ScoredPoint]:
        result = await self.client.query_points(
            collection_name,
            query=vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )
        return result.points

    async def count(
        self, collection_name: str, *, query_filter: models.Filter | None = None
    ) -> int:
        result = await self.client.count(collection_name, count_filter=query_filter, exact=True)
        return result.count

    async def close(self) -> None:
        await self.client.close()
