"""Live public-prose retrieval. This module cannot read evaluation questions or Gold."""

import math
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import tiktoken

from rag_portfolio.adapters.embedding import EmbeddingAdapter
from rag_portfolio.config import Settings
from rag_portfolio.public_medical import canonical, sha, validate_corpus
from rag_portfolio.services.hybrid_retrieval import Reranker
from rag_portfolio.services.retrieval_algorithms import (
    Candidate,
    LocalBM25Retriever,
    reciprocal_rank_fusion,
    tokenize,
)

ENCODING = tiktoken.get_encoding("cl100k_base")
STRATEGIES = ["bm25", "dense", "hybrid", "hybrid_rerank", "coverage", "context_expanded"]


def normalize(vector):
    if not vector or any(not math.isfinite(v) for v in vector):
        raise ValueError("Invalid vector")
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        raise ValueError("Zero vector")
    return [v / norm for v in vector]


def input_text(chunk):
    return f"{chunk['document_id']} | {chunk['title']} | {chunk['section']}\n{chunk['text']}"


async def build_index(corpus, output: Path, *, processing_approval=None):
    from rag_portfolio.medical_release import processing_gate

    processing_gate(corpus, processing_approval)
    if output.exists():
        raise ValueError("Index is immutable; use a new index version")
    adapter = EmbeddingAdapter(Settings())
    rows, calls = [], []
    try:
        for start in range(0, len(corpus["chunks"]), 10):
            chunks = corpus["chunks"][start : start + 10]
            vectors = await adapter.embed_documents([input_text(c) for c in chunks])
            rows.extend(
                {"chunk_id": c["chunk_id"], "text_sha256": c["text_sha256"], "vector": normalize(v)}
                for c, v in zip(chunks, vectors, strict=True)
            )
            calls.append({"batch_start": start, "count": len(chunks), **adapter.last_metadata})
            print(f"indexed {len(rows)}/{len(corpus['chunks'])}", flush=True)
    finally:
        await adapter.close()
    payload = {
        "schema_version": "public-medical-index-v1",
        "index_version": output.stem,
        "corpus_id": corpus["corpus_id"],
        "corpus_sha256": corpus["corpus_sha256"],
        "model": adapter.model,
        "dimension": adapter.dimension,
        "distance": "cosine",
        "input": "document_id|title|section|text-v1",
        "rows": rows,
        "provider_calls": calls,
    }
    payload["index_sha256"] = sha(canonical(payload))
    return payload


def validate_index(corpus, index):
    validate_corpus(corpus)
    if (
        index["corpus_sha256"] != corpus["corpus_sha256"]
        or index["corpus_id"] != corpus["corpus_id"]
        or index["index_sha256"]
        != sha(canonical({k: v for k, v in index.items() if k != "index_sha256"}))
    ):
        raise ValueError("Index/corpus pin mismatch")
    chunks = {c["chunk_id"]: c for c in corpus["chunks"]}
    if len(index["rows"]) != len(chunks) or len({r["chunk_id"] for r in index["rows"]}) != len(
        chunks
    ):
        raise ValueError("Index membership mismatch")
    for row in index["rows"]:
        if (
            row["chunk_id"] not in chunks
            or row["text_sha256"] != chunks[row["chunk_id"]]["text_sha256"]
            or len(row["vector"]) != index["dimension"]
        ):
            raise ValueError("Index row mismatch")
        normalize(row["vector"])
        if abs(sum(v * v for v in row["vector"]) - 1) > 1e-6:
            raise ValueError("Index vectors must be normalized")


def boundary(query):
    # Predeclared transparent input routing, not a calibrated no-answer classifier.
    if ("公开库" in query or "中国" in query) and any(s in query for s in ["指南", "共识"]):
        return "out_of_public_scope"
    if ("患者" in query or "我" in query) and any(
        s in query for s in ["没有", "未提供", "缺少", "个人", "病历", "填写", "个体处方"]
    ):
        return "needs_patient_data_and_clinical_review"
    return "retrieve"


def coverage_order(query, ranked, chunks):
    wanted = set(re.findall(r"\b(?:NEW0[1-6]|D161)\b", query, re.I))
    wanted = {w.upper() for w in wanted}
    terms = {t for t in tokenize(query) if len(t) > 1}
    chosen, seen_docs, seen_terms, remaining = [], set(), set(), list(ranked)
    while remaining:

        def objective(pair):
            i, candidate = pair
            c = chunks[str(candidate.chunk_id)]
            hits = {t for t in terms if t in input_text(c).casefold()}
            source_gain = int(c["document_id"] in wanted and c["document_id"] not in seen_docs)
            return (
                source_gain,
                0.8 / (i + 1) + 0.2 * len(hits - seen_terms) / max(1, len(terms)),
                -i,
            )

        i, c = max(enumerate(remaining), key=objective)
        remaining.pop(i)
        chosen.append(c)
        chunk = chunks[str(c.chunk_id)]
        seen_docs.add(chunk["document_id"])
        seen_terms.update(t for t in terms if t in input_text(chunk).casefold())
    return chosen


def context_pack(top, corpus, budget=4000, *, expanded=False):
    chunks = {c["chunk_id"]: c for c in corpus["chunks"]}
    seeds = [chunks[str(c.chunk_id)] for c in top]
    neighbors = []
    if expanded:
        # Expansion is adjacent source text, never generated summaries or Gold spans.
        for seed in seeds:
            neighbors.extend(
                c
                for c in corpus["chunks"]
                if c["segment_id"] == seed["segment_id"]
                and c["chunk_id"] != seed["chunk_id"]
                and abs(c["start"] - seed["start"]) <= 1100
            )
    selected, seen = [], set()
    for c in seeds + neighbors:
        if c["chunk_id"] in seen:
            continue
        trial = selected + [c]
        if len(ENCODING.encode(canonical(trial).decode())) <= budget:
            selected.append(c)
            seen.add(c["chunk_id"])
    return selected, len(ENCODING.encode(canonical(selected).decode()))


class PublicMedicalRetriever:
    def __init__(self, corpus, index, *, processing_approval=None, route=None):
        from rag_portfolio.medical_release import processing_gate

        processing_gate(corpus, processing_approval)
        validate_index(corpus, index)
        self.processing_approval = processing_approval
        self.route = route or boundary
        self.corpus, self.index = corpus, index
        self.chunks = {c["chunk_id"]: c for c in corpus["chunks"]}
        self.bm25 = LocalBM25Retriever({UUID(k): input_text(c) for k, c in self.chunks.items()})
        self.settings = Settings()
        if (
            self.settings.embedding_model != index["model"]
            or self.settings.embedding_dimension != index["dimension"]
        ):
            raise ValueError("Embedding configuration differs from pinned index")
        self.embedding = EmbeddingAdapter(self.settings)
        self.reranker = Reranker(
            self.settings,
            SimpleNamespace(rerank_model="qwen3.7-text-rerank", provider_timeout_seconds=30),
        )

    async def retrieve(self, query, budget=4000):
        # Recheck mutable in-memory inputs before every query as well as at startup.
        from rag_portfolio.medical_release import processing_gate

        processing_gate(self.corpus, self.processing_approval)
        validate_index(self.corpus, self.index)
        sparse = await self.bm25.search(query, limit=20)
        vector = normalize(await self.embedding.embed_query(query))
        embedding_meta = dict(self.embedding.last_metadata)
        pairs = [
            (r["chunk_id"], sum(a * b for a, b in zip(vector, r["vector"], strict=True)))
            for r in self.index["rows"]
        ]
        pairs.sort(key=lambda p: (-p[1], p[0]))
        dense = [
            Candidate(UUID(k), score, dense_rank=i, dense_score=score)
            for i, (k, score) in enumerate(pairs[:20], 1)
        ]
        hybrid = reciprocal_rank_fusion(dense, sparse)[:30]
        reranked, rerank_meta = await self.reranker.rerank(
            query, hybrid, [input_text(self.chunks[str(c.chunk_id)]) for c in hybrid]
        )
        covered = coverage_order(query, reranked, self.chunks)
        rankings = dict(
            zip(STRATEGIES, [sparse, dense, hybrid, reranked, covered, covered], strict=True)
        )
        result = {}
        for name, ranking in rankings.items():
            top = ranking[:5]
            packed, tokens = context_pack(
                top, self.corpus, budget, expanded=name == "context_expanded"
            )
            result[name] = {
                "top5": [{"chunk_id": str(c.chunk_id), "score": c.score} for c in top],
                "context": packed,
                "context_tokens": tokens,
            }
        return {
            "query": query,
            "boundary": self.route(query),
            "embedding": embedding_meta,
            "rerank": rerank_meta,
            "actual_model_calls": {"embedding": 1, "rerank": 1},
            "candidate_count": len(hybrid),
            "strategies": result,
            "ablation_note": "Single live query embedding and rerank response shared across "
            "selection ablations; no saved-query ranking backend",
        }

    async def close(self):
        await self.embedding.close()
        await self.reranker.close()
