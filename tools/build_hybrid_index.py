"""One-time real embedding build for an approved, independently pinned static corpus."""

import argparse
import asyncio
from pathlib import Path

from rag_portfolio.adapters.embedding import EmbeddingAdapter
from rag_portfolio.config import Settings
from rag_portfolio.services.hybrid_contract import canonical, digest
from rag_portfolio.services.hybrid_retrieval import (
    Corpus,
    VectorIndex,
    chunks,
    pinned,
    validate_vector,
)


async def main(args):
    corpus = Corpus.model_validate_json(pinned(str(args.corpus), args.corpus_sha256))
    rows = chunks(corpus)
    texts = [d.text[s.start : s.end] for d, s in rows.values()]
    provider = Settings(embedding_timeout_seconds=15)
    embedding = EmbeddingAdapter(provider)
    try:
        async with asyncio.timeout(20):
            vectors = await embedding.embed_documents(texts)  # Single bounded API call, no cache.
    finally:
        await embedding.close()
    for vector in vectors:
        validate_vector(vector, provider.embedding_dimension)
    index = VectorIndex(
        schema_version="hybrid-index-v1",
        namespace="isolated-hybrid-demo-v1",
        corpus_sha256=args.corpus_sha256,
        embedding_model=provider.embedding_model,
        embedding_dimension=provider.embedding_dimension,
        metric="cosine",
        vectors={str(cid): vector for cid, vector in zip(rows, vectors, strict=True)},
        chunk_hashes={
            str(cid): digest(text.encode()) for cid, text in zip(rows, texts, strict=True)
        },
    )
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an index; use a new artifact path")
    raw = canonical(index.model_dump(mode="json"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(raw)
    print(
        {
            "execution": "real-provider",
            "embedding_calls": 1,
            "chunks": len(rows),
            "model": provider.embedding_model,
            "dimension": provider.embedding_dimension,
            "index_sha256": digest(raw),
        }
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--corpus-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
