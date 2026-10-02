from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from rag_portfolio.adapters.vector import QdrantConnection, VectorAdapterError, payload_filter
from rag_portfolio.config import Settings

COLLECTION = "test_dense"
REVISION = "test:model:d3:dense:title-section-text-v1"


@pytest.fixture
async def vector(monkeypatch):
    # The installed client's bundled in-memory implementation needs no service or new infra.
    client = AsyncQdrantClient(location=":memory:")
    monkeypatch.setattr("rag_portfolio.adapters.vector.AsyncQdrantClient", lambda **_: client)
    connection = QdrantConnection(Settings(_env_file=None))
    try:
        yield connection
    finally:
        await connection.close()


def point(vector, revision=REVISION):
    chunk_id = str(uuid4())
    return models.PointStruct(
        id=chunk_id,
        vector=vector,
        payload={
            "chunk_id": chunk_id,
            "embedding_revision": revision,
            "version_id": "test-version",
        },
    )


async def test_upsert_is_idempotent_and_search_returns_chunk_ids_in_cosine_order(vector):
    points = [point([0.0, 1.0, 0.0]), point([1.0, 0.0, 0.0])]
    for _ in range(2):
        await vector.upsert(COLLECTION, points, dimension=3, embedding_revision=REVISION)
        assert await vector.count(COLLECTION) == 2
    hits = await vector.search(
        COLLECTION,
        [1.0, 0.0, 0.0],
        query_filter=payload_filter({"embedding_revision": REVISION, "version_id": "test-version"}),
    )
    assert [hit.id for hit in hits] == [points[1].id, points[0].id]
    assert hits[0].score == pytest.approx(1)
    assert hits[0].vector is None
    assert (
        await vector.search(
            COLLECTION, [1.0, 0.0, 0.0], query_filter=payload_filter({"version_id": "other"})
        )
        == []
    )


@pytest.mark.parametrize("revision", ["another-revision", None])
async def test_collection_with_mixed_or_missing_revision_is_rejected(vector, revision):
    await vector.upsert(
        COLLECTION, [point([1.0, 0.0, 0.0])], dimension=3, embedding_revision=REVISION
    )
    foreign = point([0.0, 1.0, 0.0], revision)
    if revision is None:
        foreign.payload.pop("embedding_revision")
    await vector.client.upsert(COLLECTION, [foreign])
    with pytest.raises(VectorAdapterError, match="different/missing revision"):
        await vector.upsert(
            COLLECTION, [point([1.0, 0.0, 0.0])], dimension=3, embedding_revision=REVISION
        )
    assert await vector.count(COLLECTION) == 2


async def test_recreate_clears_old_points_and_allows_new_revision(vector):
    await vector.upsert(
        COLLECTION, [point([1.0, 0.0, 0.0])], dimension=3, embedding_revision=REVISION
    )
    await vector.recreate_collection(COLLECTION, dimension=3, embedding_revision="new-revision")
    assert await vector.count(COLLECTION) == 0
    await vector.upsert(
        COLLECTION,
        [point([1.0, 0.0, 0.0], "new-revision")],
        dimension=3,
        embedding_revision="new-revision",
    )
    assert await vector.count(COLLECTION) == 1


@pytest.mark.parametrize(
    "config",
    [
        models.VectorParams(size=2, distance=models.Distance.COSINE),
        models.VectorParams(size=3, distance=models.Distance.DOT),
        {"named": models.VectorParams(size=3, distance=models.Distance.COSINE)},
    ],
)
async def test_incompatible_collection_configuration_is_rejected(vector, config):
    await vector.client.create_collection(COLLECTION, vectors_config=config)
    with pytest.raises(VectorAdapterError, match="configuration differs"):
        await vector.ensure_collection(COLLECTION, dimension=3, embedding_revision=REVISION)


@pytest.mark.parametrize("problem", ["id", "revision", "dimension"])
async def test_inconsistent_point_is_rejected_before_write(vector, problem):
    candidate = point([1.0, 0.0, 0.0])
    if problem == "id":
        candidate.payload["chunk_id"] = str(uuid4())
    elif problem == "revision":
        candidate.payload["embedding_revision"] = "other"
    else:
        candidate.vector = [1.0, 0.0]
    with pytest.raises(VectorAdapterError, match="mismatch"):
        await vector.upsert(COLLECTION, [candidate], dimension=3, embedding_revision=REVISION)
    assert not await vector.client.collection_exists(COLLECTION)
