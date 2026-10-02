"""Index and evaluate the fixed, approved hospital-scene-guide revision 2 corpus."""

import argparse
import asyncio
import sys
from contextlib import AsyncExitStack
from uuid import UUID

from qdrant_client import models

from rag_portfolio.adapters.embedding import EmbeddingAdapter, EmbeddingAdapterError
from rag_portfolio.adapters.vector import QdrantConnection, VectorAdapterError, payload_filter
from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.services.dense_retrieval import (
    EMBEDDING_INPUT_VERSION,
    ApprovedCorpus,
    DenseRetrievalError,
    build_embedding_revision,
    build_embedding_text,
    build_qdrant_payload,
    hydrate_chunks,
    load_approved_corpus,
)

VERSION_ID = UUID("cc725d7d-f8a9-455d-a613-3ccf71705fae")
COLLECTION = "rag_smoke_hospital_scene_v2"
CHUNKER = "simple-v1:markdown:c800:o80"
TOP_K = 5
GOLD = {
    "岳阳医院二楼有哪些诊室？": 6,
    "智能步道康复室在几楼？": 7,
    "岳阳医院四楼可以做哪些评估？": 8,
    "五楼数据中心主要展示什么信息？": 9,
    "怎么返回社交岛？": 2,
    "岳阳医院一共有几层？": 4,
}


def validate_smoke_corpus(corpus: ApprovedCorpus) -> None:
    if (
        corpus.version.id != VERSION_ID
        or corpus.document.external_key != "hospital-scene-guide"
        or corpus.version.revision != 2
        or corpus.version.metadata_json.get("format") != "markdown"
        or len(corpus.chunks) != 10
        or [chunk.ordinal for chunk in corpus.chunks] != list(range(10))
        or any(chunk.chunker_version != CHUNKER for chunk in corpus.chunks)
    ):
        raise DenseRetrievalError("Corpus differs from hospital-scene-guide revision 2 / 10 chunks")


async def run_smoke(settings: Settings, version_id: UUID, *, recreate: bool = False) -> None:
    if version_id != VERSION_ID:
        raise DenseRetrievalError("This smoke only indexes the specified revision 2 version ID")
    if (
        settings.embedding_provider != "alibaba_dashscope"
        or settings.embedding_model != "qwen3.7-text-embedding"
        or settings.embedding_dimension != 1024
        or settings.embedding_output_type != "dense"
    ):
        raise DenseRetrievalError(
            "Smoke requires alibaba_dashscope/qwen3.7-text-embedding/1024/dense"
        )
    async with AsyncExitStack() as resources:
        database = Database(settings)
        resources.push_async_callback(database.close)
        async with database.sessions() as session:
            corpus = await load_approved_corpus(session, settings.tenant_id, version_id)
        validate_smoke_corpus(corpus)
        document, version, chunks = corpus.document, corpus.version, corpus.chunks
        print(f"Version: {version.id} revision={version.revision} status={version.review_status}")
        print(f"Document: {document.id} {document.external_key} — {document.title}")
        print(f"Chunks loaded: {len(chunks)}")
        print(f"Chunker: {CHUNKER}")

        texts = [
            build_embedding_text(document.title, chunk.section_path, chunk.text) for chunk in chunks
        ]
        revision = build_embedding_revision(settings)
        print(f"Embedding input version: {EMBEDDING_INPUT_VERSION}")
        print(f"Embedding revision: {revision}")
        embedding = EmbeddingAdapter(settings)
        resources.push_async_callback(embedding.close)
        vectors = await embedding.embed_documents(texts)
        print(
            f"Embedding: provider={settings.embedding_provider} model={settings.embedding_model} "
            f"vectors={len(vectors)} dimension={settings.embedding_dimension}"
        )

        qdrant = QdrantConnection(settings)
        resources.push_async_callback(qdrant.close)
        prepare = qdrant.recreate_collection if recreate else qdrant.ensure_collection
        await prepare(
            COLLECTION, dimension=settings.embedding_dimension, embedding_revision=revision
        )
        print(f"Collection: {COLLECTION} distance=COSINE recreate={recreate}")
        scope = payload_filter(
            {
                "tenant_id": version.tenant_id,
                "kb_id": str(version.kb_id),
                "document_id": str(document.id),
                "version_id": str(version.id),
                "chunker_version": CHUNKER,
                "embedding_revision": revision,
                "embedding_input_version": EMBEDDING_INPUT_VERSION,
            }
        )
        if await qdrant.count(COLLECTION) != await qdrant.count(COLLECTION, query_filter=scope):
            raise DenseRetrievalError("Collection contains another corpus; use --recreate")
        points = [
            models.PointStruct(
                id=str(chunk.id),
                vector=vector,
                payload=build_qdrant_payload(document, version, chunk, revision),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        await qdrant.upsert(
            COLLECTION, points, dimension=settings.embedding_dimension, embedding_revision=revision
        )
        count = await qdrant.count(COLLECTION)
        print(f"Points: {count}")
        if count != len(chunks):
            raise DenseRetrievalError(
                "Collection point count differs from 10 chunks; use --recreate"
            )

        ranks: list[int | None] = []
        short_chunk_ranks: list[tuple[str, int | None]] = []
        for query, gold in GOLD.items():
            query_vector = await embedding.embed_query(query)
            hits = await qdrant.search(COLLECTION, query_vector, query_filter=scope, top_k=TOP_K)
            if len(hits) != TOP_K:
                raise DenseRetrievalError("Expected five hits from the ten-point smoke collection")
            try:
                ids = [UUID(str(hit.id)) for hit in hits]
            except ValueError:
                raise DenseRetrievalError("Qdrant returned a non-UUID point ID") from None
            # A fresh SQL read after each search, not a lookup in the initially loaded chunks.
            async with database.sessions() as session:
                hydrated = await hydrate_chunks(session, corpus, ids)
            print("\n" + "=" * 60)
            print(f"Query: {query}\n")
            ordinals = []
            for rank, (hit, (source, chunk)) in enumerate(zip(hits, hydrated, strict=True), 1):
                if hit.payload != build_qdrant_payload(source, version, chunk, revision):
                    raise DenseRetrievalError(
                        "Qdrant payload differs from PostgreSQL; reindex required"
                    )
                ordinals.append(chunk.ordinal)
                print(f"[{rank}] score={hit.score:.6f} chunk_id={chunk.id} ordinal={chunk.ordinal}")
                print(f"title: {source.title}")
                print(f"section:\n{chunk.section_path}\ntext:\n{chunk.text}\n")
            gold_rank = ordinals.index(gold) + 1 if gold in ordinals else None
            ranks.append(gold_rank)
            short_chunk_ranks.append((query, ordinals.index(4) + 1 if 4 in ordinals else None))
            print(f"Gold ordinal: {gold}")
            print(f"Gold rank: {gold_rank if gold_rank is not None else 'not in Top-5'}")
            for cutoff in (1, 3, 5):
                print(f"Hit@{cutoff}: {int(gold_rank is not None and gold_rank <= cutoff)}")
            print(f"Reciprocal rank: {1 / gold_rank if gold_rank is not None else 0:.6f}")

        print("\n" + "=" * 60)
        print(f"Queries: {len(ranks)}")
        for cutoff in (1, 3, 5):
            recall = sum(rank is not None and rank <= cutoff for rank in ranks) / len(ranks)
            print(f"Recall@{cutoff}: {recall:.6f}")
        print(f"MRR: {sum(1 / rank if rank is not None else 0 for rank in ranks) / len(ranks):.6f}")
        print("\nShort chunk (ordinal 4) ranks:")
        for query, rank in short_chunk_ranks:
            print(f"  {query}: {rank if rank is not None else 'not in Top-5'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-id", type=UUID, required=True, choices=[VERSION_ID])
    parser.add_argument("--recreate", action="store_true", help=f"Delete and recreate {COLLECTION}")
    args = parser.parse_args()
    # Preserve Chinese text in Windows consoles and redirected UTF-8 smoke reports.
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(
            run_smoke(Settings(), args.version_id, recreate=args.recreate),
            loop_factory=loop_factory,
        )
    except (EmbeddingAdapterError, VectorAdapterError, DenseRetrievalError) as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:
        # Provider/SQL exceptions can include URLs, request bodies or credentials.
        print(f"FAIL: {type(exc).__name__}; check configured PostgreSQL/Qdrant connections")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
