"""Real query vectors, full-text BM25, RRF, provider rerank and atomic context packing.

Only approved static manifests enter this module. No evaluation assets or query cache.
"""

import asyncio
import hmac
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rag_portfolio.adapters.embedding import EmbeddingAdapter, EmbeddingAdapterError
from rag_portfolio.services.errors import EvaluationError
from rag_portfolio.services.hybrid_contract import (
    Model,
    RetrievalQuery,
    RetrievalResponse,
    Snippet,
    canonical,
    digest,
    estimate_tokens,
)
from rag_portfolio.services.retrieval_algorithms import (
    Candidate,
    LocalBM25Retriever,
    reciprocal_rank_fusion,
    tokenize,
)

# Unigram-only overlap and question particles are too weak to establish topical support.
# Fixed language rule, independent of corpus Gold/query identifiers.
QUESTION_TERMS = {"多少", "什么", "如何", "是否", "怎样", "怎么", "哪些", "可以", "应该"}


def lexical_support(query: str, text: str) -> bool:
    terms = {t for t in tokenize(query) if len(t) > 1 and t not in QUESTION_TERMS}
    return bool(terms.intersection(tokenize(text)))


class HybridSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_HYBRID_", env_file=None, extra="ignore")
    enabled: bool = False
    environment: Literal["development", "test", "production"] = "development"
    token: SecretStr = SecretStr("")
    tenant_id: str = "hybrid-demo"
    scope_key: str = "hybrid-demo"
    domains: list[Literal["health", "product", "help"]] = Field(
        default_factory=lambda: ["help"], min_length=1, max_length=3
    )
    corpus_path: str = ""
    corpus_sha256: str = ""
    index_path: str = ""
    index_sha256: str = ""
    timeout_seconds: float = Field(default=12, gt=0, le=15)
    provider_timeout_seconds: float = Field(default=5, gt=0, le=6)
    max_requests: int = Field(default=200, ge=1, le=1000)
    rerank_model: str = "qwen3.7-text-rerank"
    dense_min_cosine: float = Field(default=0.4, ge=0, le=1)

    @model_validator(mode="after")
    def gate(self):
        if self.enabled and (
            self.environment == "production"
            or len(self.token.get_secret_value()) < 32
            or len(self.corpus_sha256) != 64
            or len(self.index_sha256) != 64
            or not self.corpus_path
            or not self.index_path
        ):
            raise ValueError("Hybrid demo requires nonproduction configuration, token and pins")
        return self


class Section(Model):
    section_id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=200)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    page: int | None = Field(default=None, ge=1)


class Document(Model):
    code: str = Field(pattern=r"^[a-z0-9-]+$")
    version: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=200)
    domain: Literal["health", "help", "product"]
    source: str = Field(pattern=r"^(?:repo|https)://[^\s]+$")
    license: Literal["CC0-1.0"]
    approved_by: str = Field(min_length=1)
    approved: Literal[True]
    text: str = Field(min_length=1, max_length=20000)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sections: list[Section] = Field(min_length=1, max_length=12)


class Corpus(Model):
    schema_version: Literal["hybrid-corpus-v1"]
    corpus_version: str
    tenant_id: str
    scope_key: str
    approval: Literal["technical_demo_only"]
    clinical_approval: Literal[False]
    documents: list[Document] = Field(min_length=6, max_length=10)

    @model_validator(mode="after")
    def verify(self):
        if len({d.code for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate source code")
        if len({d.source for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate source URI")
        for d in self.documents:
            if digest(d.text.encode()) != d.text_sha256 or not d.approved_by.strip():
                raise ValueError("Source hash/approval mismatch")
            if len({s.section_id for s in d.sections}) != len(d.sections):
                raise ValueError("Duplicate section")
            for s in d.sections:
                if not 0 <= s.start < s.end <= len(d.text) or s.end - s.start > 1600:
                    raise ValueError("Invalid chunk span")
        return self


class VectorIndex(Model):
    schema_version: Literal["hybrid-index-v1"]
    namespace: Literal["isolated-hybrid-demo-v1"]
    corpus_sha256: str
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    metric: Literal["cosine"]
    vectors: dict[str, list[float]]
    chunk_hashes: dict[str, str]


def pinned(path: str, sha: str) -> bytes:
    p = Path(path).resolve()
    if any(part.lower() in {".local", "outputs"} for part in p.parts):
        raise ValueError("Private paths are prohibited")
    if not p.is_file() or p.stat().st_size > 5_000_000:
        raise ValueError("Missing/oversized manifest")
    raw = p.read_bytes()
    if not hmac.compare_digest(digest(raw), sha):
        raise ValueError("Manifest pin drift")
    return raw


def chunks(corpus: Corpus) -> dict[UUID, tuple[Document, Section]]:
    return {
        uuid5(NAMESPACE_URL, f"{d.source}@{d.version}#{s.section_id}"): (d, s)
        for d in corpus.documents
        for s in d.sections
    }


def validate_vector(vector: list[float], dimension: int) -> list[float]:
    if (
        len(vector) != dimension
        or not all(type(v) in {float, int} and math.isfinite(v) for v in vector)
        or not math.isfinite(norm := math.hypot(*vector))
        or norm <= 0
    ):
        raise ValueError("Invalid dense vector dimension/norm")
    return [v / norm for v in vector]


class Reranker:
    """Native provider call, no ranking cache; index is mapped to the actual input list."""

    def __init__(self, provider, config, *, transport=None):
        self.model = config.rerank_model
        self.url = provider.embedding_base_url.rstrip("/") + (
            "/services/rerank/text-rerank/text-rerank"
        )
        self.client = httpx.AsyncClient(
            timeout=config.provider_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Authorization": "Bearer " + provider.embedding_api_key.get_secret_value()},
        )

    async def rerank(self, query, candidates, texts):
        try:
            async with self.client.stream(
                "POST",
                self.url,
                json={
                    "model": self.model,
                    "input": {"query": query, "documents": texts},
                    "parameters": {"top_n": len(texts)},
                },
            ) as response:
                if response.status_code != 200:
                    raise ValueError("Reranker HTTP failure")
                raw = bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    if len(raw) > 65536:
                        raise ValueError("Reranker response too large")
            import json

            body = json.loads(raw)
            rows = body["output"]["results"]
            if len(rows) != len(candidates):
                raise ValueError("Reranker count mismatch")
            seen, result = set(), []
            for r in rows:
                i, score = r["index"], r["relevance_score"]
                if (
                    type(i) is not int
                    or i in seen
                    or not 0 <= i < len(candidates)
                    or type(score) not in {int, float}
                    or not math.isfinite(score)
                ):
                    raise ValueError("Reranker index/score mismatch")
                seen.add(i)
                result.append(replace(candidates[i], score=float(score)))
            result.sort(key=lambda c: (-c.score, str(c.chunk_id)))
            return result, {"request_id": body.get("request_id"), "usage": body.get("usage", {})}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("reranker_unavailable") from None

    async def close(self):
        await self.client.aclose()


def coverage_order(query, ranked, rows):
    """Query-only source/facet coverage; never consult expected answers or test identifiers."""
    wanted_sources = {d.code for d, _ in rows.values() if d.code in query or d.title in query}
    # Source-balanced selection for explicit comparisons, otherwise ordinary relevance.
    targets = []
    for term in tokenize(query):
        if len(term) > 1 and term not in targets:
            targets.append(term)
    covered, sources, remaining, chosen = set(), set(), list(ranked), []
    while remaining:

        def objective(pair):
            i, c = pair
            d, s = rows[c.chunk_id]
            text = d.text[s.start : s.end]
            affinity = {t for t in targets if t in text.casefold()}
            source_gain = int(d.code in wanted_sources and d.code not in sources)
            gain = len(affinity - covered) / max(1, len(targets))
            return (source_gain, 0.55 / (i + 1) + 0.45 * gain, -i)

        i, c = max(enumerate(remaining), key=objective)
        remaining.pop(i)
        d, s = rows[c.chunk_id]
        sources.add(d.code)
        covered.update(t for t in targets if t in d.text[s.start : s.end].casefold())
        chosen.append(c)
    return chosen


class HybridPipeline:
    def __init__(self, config, provider, *, embedding=None, reranker=None):
        self.config = config
        self.corpus = Corpus.model_validate_json(pinned(config.corpus_path, config.corpus_sha256))
        self.index = VectorIndex.model_validate_json(pinned(config.index_path, config.index_sha256))
        self.rows = chunks(self.corpus)
        self.texts = {cid: d.text[s.start : s.end] for cid, (d, s) in self.rows.items()}
        if (
            (self.corpus.tenant_id, self.corpus.scope_key) != (config.tenant_id, config.scope_key)
            or self.index.corpus_sha256 != config.corpus_sha256
            or self.index.embedding_model != provider.embedding_model
            or self.index.embedding_dimension != provider.embedding_dimension
            or set(self.index.vectors) != {str(cid) for cid in self.rows}
            or self.index.chunk_hashes
            != {str(cid): digest(text.encode()) for cid, text in self.texts.items()}
        ):
            raise ValueError("Index/corpus/model mismatch")
        self.vectors = {
            UUID(cid): validate_vector(v, self.index.embedding_dimension)
            for cid, v in self.index.vectors.items()
        }
        self.sparse = {
            domain: LocalBM25Retriever(texts)
            for domain in {d.domain for d in self.corpus.documents}
            if (
                texts := {
                    cid: text
                    for cid, text in self.texts.items()
                    if self.rows[cid][0].domain == domain
                }
            )
        }
        self.embedding = embedding or EmbeddingAdapter(provider)
        self.reranker = reranker or Reranker(provider, config)
        self.requests = 0
        self.lock = asyncio.Semaphore(2)

    async def close(self):
        await self.embedding.close()
        await self.reranker.close()

    def check_pins(self):
        pinned(self.config.corpus_path, self.config.corpus_sha256)
        pinned(self.config.index_path, self.config.index_sha256)

    async def search(self, q: RetrievalQuery) -> RetrievalResponse:
        self.check_pins()
        self.requests += 1
        if self.requests > self.config.max_requests:
            raise ValueError("provider_request_budget_exhausted")
        if (
            q.corpus_version != self.corpus.corpus_version
            or q.corpus_sha256 != self.config.corpus_sha256
            or q.index_sha256 != self.config.index_sha256
        ):
            raise ValueError("version_unavailable")
        async with asyncio.timeout(self.config.timeout_seconds), self.lock:
            return await self._search(q)

    async def _search(self, q):
        start = time.monotonic()
        dense, sparse, degradation, calls = [], [], [], {"embedding": 0, "rerank": 0}
        stages = {
            "source_kind": "synthetic_technical_documents",
            "loaded_domains": sorted({d.domain for d in self.corpus.documents}),
            "embedding_model": self.index.embedding_model,
            "dimension": self.index.embedding_dimension,
        }
        ids = [cid for cid in self.rows if self.rows[cid][0].domain == q.domain]
        if ids and q.strategy != "bm25":
            calls["embedding"] += 1
            try:
                async with asyncio.timeout(self.config.provider_timeout_seconds):
                    vector = validate_vector(
                        await self.embedding.embed_query(q.query), self.index.embedding_dimension
                    )
                stages["query_vector_sha256"] = digest(canonical({"vector": vector}))
                stages["embedding_provider"] = getattr(self.embedding, "last_metadata", {})
                scored = [
                    (cid, sum(a * b for a, b in zip(vector, self.vectors[cid], strict=True)))
                    for cid in ids
                ]
                scored.sort(key=lambda x: (-x[1], str(x[0])))
                dense = [
                    Candidate(cid, score, dense_rank=i, dense_score=score)
                    for i, (cid, score) in enumerate(scored, 1)
                    if score >= self.config.dense_min_cosine
                ][:20]
            except (EmbeddingAdapterError, ValueError, TimeoutError):
                degradation.append("dense_unavailable")
        if ids and q.strategy != "dense":
            try:
                sparse = await self.sparse[q.domain].search(q.query, limit=20)
                stages["bm25_rejected_weak_overlap"] = [
                    str(c.chunk_id)
                    for c in sparse
                    if not lexical_support(q.query, self.texts[c.chunk_id])
                ]
                sparse = [c for c in sparse if lexical_support(q.query, self.texts[c.chunk_id])]
            except (ValueError, KeyError, EvaluationError):
                degradation.append("bm25_unavailable")
        ranked = (
            sparse
            if q.strategy == "bm25"
            else dense
            if q.strategy == "dense"
            else reciprocal_rank_fusion(dense, sparse)
        )

        def trace(hits):
            return [{"chunk_id": str(c.chunk_id), "score": c.score} for c in hits]

        stages.update(dense=trace(dense), bm25=trace(sparse), rrf=trace(ranked))
        candidate_count = len(ranked)
        applied, rerank_meta = False, {}
        use_rerank = q.strategy in {"hybrid_rerank", "hybrid_selection"}
        if ranked and use_rerank:
            calls["rerank"] += 1
            try:
                ranked, rerank_meta = await self.reranker.rerank(
                    q.query, ranked, [self.texts[c.chunk_id] for c in ranked]
                )
                applied = True
            except ValueError:
                degradation.append("reranker_unavailable")
        stages.update(rerank=trace(ranked) if applied else [], rerank_provider=rerank_meta)
        post_count = len(ranked)
        if q.strategy == "hybrid_selection":
            ranked = coverage_order(q.query, ranked, self.rows)
        stages["selection"] = trace(ranked)
        evidence, used, omitted = [], 0, False
        for c in ranked:
            if len(evidence) >= q.top_k:
                break
            d, s = self.rows[c.chunk_id]
            text = self.texts[c.chunk_id]
            snippet = Snippet(
                source_id=uuid5(NAMESPACE_URL, d.source),
                chunk_id=c.chunk_id,
                doc_ref=f"{d.code}@{d.version}#{s.section_id}",
                document_version=d.version,
                title=d.title,
                source=d.source,
                section=s.title,
                section_id=s.section_id,
                page=s.page,
                start=s.start,
                end=s.end,
                text=text,
                text_sha256=digest(text.encode()),
                score=float(c.score),
            )
            cost = estimate_tokens(snippet.model_dump(mode="json"))
            if used + cost > q.context_budget:
                omitted = True
                continue
            used += cost
            evidence.append(snippet)
        if omitted:
            degradation.append("context_budget")
        status = ("partial" if degradation else "success") if evidence else "unavailable"
        code = (
            "degraded"
            if evidence and degradation
            else "ok"
            if evidence
            else "context_overflow"
            if ranked
            else "retrieval_unavailable"
            if degradation
            else "no_evidence"
        )
        return RetrievalResponse(
            request_id=q.request_id,
            retrieval_run_id=uuid4(),
            strategy=q.strategy,
            scope_key=self.corpus.scope_key,
            domain=q.domain,
            corpus_version=self.corpus.corpus_version,
            corpus_sha256=self.config.corpus_sha256,
            index_sha256=self.config.index_sha256,
            status=status,
            code=code,
            elapsed_ms=(time.monotonic() - start) * 1000,
            reranker_used=self.config.rerank_model if use_rerank else None,
            rerank_applied=applied,
            degradation=degradation,
            candidate_count=candidate_count,
            post_rerank_count=post_count,
            selected_count=len(evidence),
            budget_truncated=omitted,
            context_tokens_estimated=used,
            model_calls=calls,
            stages=stages,
            evidence=evidence,
        )
