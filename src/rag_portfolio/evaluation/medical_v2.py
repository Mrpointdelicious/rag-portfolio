"""Medical v2: query-only source routing, conservative deduplication and real reranking."""

import json
import math
import re
import time
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from qdrant_client import models

from rag_portfolio.evaluation.medical_corpus import digest, load_corpus, text_digest
from rag_portfolio.evaluation.medical_runner import (
    EmbeddingCache,
    evidence_card,
    point_payload,
    verify_index,
)
from rag_portfolio.evaluation.medical_schema import (
    MedicalGold,
    MedicalSuite,
    case_metrics,
    test_seal,
    validate_gold,
)
from rag_portfolio.evaluation.retrievers import (
    BM25_B,
    BM25_K1,
    TOKENIZER_VERSION,
    Candidate,
    LocalBM25Retriever,
    reciprocal_rank_fusion,
)
from rag_portfolio.literature_backup import LiteratureError, write_json

EXPERIMENT = "medical_rehab_v2"
DOMAIN = "medical_knowledge"
RERANK_MODEL = "qwen3.7-text-rerank"
INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query."
VARIANTS = (
    "dense",
    "bm25",
    "hybrid",
    "hybrid_dedup",
    "source_routed",
    "reranked_hybrid",
    "source_routed_rerank",
)
CONFIG = {
    "candidate_depth": 20,
    "final_depth": 5,
    "rrf_k": 60,
    "bm25_k1": BM25_K1,
    "bm25_b": BM25_B,
    "tokenizer_version": TOKENIZER_VERSION,
    "embedding_input": "title-section-text-v1",
    "routing": "explicit-title-alias-v1",
    "routing_query": "remove-matched-titles-for-content-search-v1",
    "source_merge": "round-robin-query-order-v1",
    "dedup": "same-page-90pct-overlap-and-identical-text-v1",
    "rerank_model": RERANK_MODEL,
    "rerank_input": "title-section-text-v1",
    "rerank_instruction": INSTRUCTION,
    "selection": "complete_evidence_at_5,required_evidence_coverage,mrr_at_5,variant-order",
}


class SuiteV2(MedicalSuite):
    experiment_id: Literal["medical_rehab_v2"]


class GoldV2(MedicalGold):
    experiment_id: Literal["medical_rehab_v2"]


def v2_path(root: Path, path: Path, *, public: bool = False) -> Path:
    base = (root / ("eval" if public else ".local") / EXPERIMENT).resolve()
    result = path.resolve()
    if result == base or not result.is_relative_to(base):
        raise LiteratureError("Medical v2 paths must remain in its own namespace")
    return result


def anchor_span(page: dict, anchor: str) -> dict:
    positions = [i for i, char in enumerate(page["text"]) if not char.isspace()]
    compact = "".join(page["text"][i] for i in positions)
    matches = list(re.finditer(re.escape(re.sub(r"\s+", "", anchor)), compact))
    if len(matches) != 1:
        raise LiteratureError(
            f"Missing/ambiguous anchor: {page['document_id']}/{page['pdf_page']}/{anchor}"
        )
    start, end = positions[matches[0].start()], positions[matches[0].end() - 1] + 1
    return {
        "document_id": page["document_id"],
        "pdf_page": page["pdf_page"],
        "start_char": start,
        "end_char": end,
        "text_sha256": text_digest(page["text"][start:end]),
    }


def freeze(root: Path) -> GoldV2:
    base = root / "eval" / EXPERIMENT
    suite = SuiteV2.model_validate_json((base / "cases.json").read_text(encoding="utf8"))
    reviews = json.loads((base / "source_reviews.json").read_text(encoding="utf8"))
    if (
        reviews.get("experiment_id") != EXPERIMENT
        or reviews.get("domain") != DOMAIN
        or reviews.get("clinical_approval") is not False
        or reviews.get("review_scope") != "source_text_and_provenance"
    ):
        raise LiteratureError("Explicit medical v2 review required")
    manifest, pages, chunks = load_corpus(
        root / ".local/medical_rehab_v1/corpus/manifest.json", root / "doc"
    )
    page_map = {(p["document_id"], p["pdf_page"]): p for p in pages}
    cases = []
    for review in reviews["cases"]:
        groups = [
            {
                "id": g["id"],
                "support": g["support"],
                "alternatives": [
                    anchor_span(page_map[(r["document_id"], r["pdf_page"])], r["anchor"])
                    for r in g["alternatives"]
                ],
            }
            for g in review["required_groups"]
        ]
        cases.append(
            {k: v for k, v in review.items() if k != "required_groups"}
            | {"required_groups": groups}
        )
    gold = GoldV2.model_validate(
        {
            "schema_version": 1,
            "domain": DOMAIN,
            "experiment_id": EXPERIMENT,
            "corpus_fingerprint": manifest["corpus_fingerprint"],
            "suite_fingerprint": digest(suite.model_dump(mode="json")),
            "status": "frozen_technical_review",
            "clinical_approval": False,
            "cases": cases,
            "test_seal": test_seal(suite, cases, manifest["corpus_fingerprint"]),
        }
    )
    validate_gold(gold, suite, manifest, pages, chunks)
    destination = base / "gold.json"
    if destination.exists() and json.loads(
        destination.read_text(encoding="utf8")
    ) != gold.model_dump(mode="json"):
        raise LiteratureError("V2 Gold is sealed; use a new revision to change it")
    write_json(destination, gold.model_dump(mode="json"))
    return gold


def normalize_alias(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def resolve_sources(query: str, aliases: dict[str, list[str]]) -> tuple[list[str], str]:
    """No case IDs, candidate_sources, filters or Gold enter routing."""
    positions = [i for i, char in enumerate(query) for part in normalize_alias(char) if part]
    normalized = "".join(normalize_alias(char) for char in query)
    found, removed = [], set()
    for doc, names in aliases.items():
        hits = [
            (normalized.find(normalize_alias(n)), normalize_alias(n))
            for n in names
            if normalize_alias(n) in normalized
        ]
        if hits:
            found.append((min(i for i, _ in hits), doc))
            for index, name in hits:
                removed.update(range(positions[index], positions[index + len(name) - 1] + 1))
    content = "".join(" " if i in removed else char for i, char in enumerate(query))
    content = re.sub(r"\s+", " ", content).strip()
    return [doc for _, doc in sorted(found)], content or query


def deduplicate(candidates: list[Candidate], lookup: dict) -> list[Candidate]:
    """Drop only identical text with near-identical spans on the same source page."""
    kept = []
    for candidate in candidates:
        row = lookup[str(candidate.chunk_id)]
        duplicate = False
        for other in kept:
            previous = lookup[str(other.chunk_id)]
            if (row["document_id"], row["pdf_page_start"]) != (
                previous["document_id"],
                previous["pdf_page_start"],
            ):
                continue
            overlap = max(
                0,
                min(row["end_char"], previous["end_char"])
                - max(row["start_char"], previous["start_char"]),
            )
            a = re.sub(r"\s+", " ", row["text"]).strip()
            b = re.sub(r"\s+", " ", previous["text"]).strip()
            if (
                overlap
                / min(
                    row["end_char"] - row["start_char"],
                    previous["end_char"] - previous["start_char"],
                )
                >= 0.9
                and a == b
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


def balanced(candidates: list[Candidate], sources: list[str], lookup: dict) -> list[Candidate]:
    if len(sources) < 2:
        return candidates
    queues = [
        [c for c in candidates if lookup[str(c.chunk_id)]["document_id"] == doc] for doc in sources
    ]
    result = []
    while any(queues):
        for queue in queues:
            if queue:
                result.append(queue.pop(0))
    return result


def validate_rerank_results(rows, count: int) -> list[dict]:
    if not isinstance(rows, list) or len(rows) != count:
        raise LiteratureError("Reranker returned incomplete results")
    for row in rows:
        if not isinstance(row, dict) or type(row.get("index")) is not int:
            raise LiteratureError("Invalid reranker index")
        score = row.get("relevance_score")
        if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
            raise LiteratureError("Invalid reranker score")
    if {r["index"] for r in rows} != set(range(count)):
        raise LiteratureError("Reranker indices are not a permutation")
    return sorted(rows, key=lambda r: (-r["relevance_score"], r["index"]))


class Reranker:
    def __init__(self, settings, path: Path, *, transport=None):
        if (
            settings.embedding_provider != "alibaba_dashscope"
            or not settings.embedding_base_url.rstrip("/").endswith("/api/v1")
        ):
            raise LiteratureError(
                "Native DashScope reranking requires the configured embedding origin"
            )
        self.revision = digest(
            {
                "model": RERANK_MODEL,
                "base_url": settings.embedding_base_url,
                "instruction": INSTRUCTION,
                "input": CONFIG["rerank_input"],
            }
        )
        self.path = path
        self.values = {}
        if path.exists():
            cache = json.loads(path.read_text(encoding="utf8"))
            if (
                cache.get("domain") != DOMAIN
                or cache.get("experiment_id") != EXPERIMENT
                or cache.get("revision") != self.revision
            ):
                raise LiteratureError("Foreign/stale rerank cache")
            self.values = cache["values"]
        self.url = (
            settings.embedding_base_url.rstrip("/") + "/services/rerank/text-rerank/text-rerank"
        )
        self.client = httpx.AsyncClient(
            timeout=60,
            transport=transport,
            headers={"Authorization": "Bearer " + settings.embedding_api_key.get_secret_value()},
        )
        self.calls, self.cache_hits, self.tokens = 0, 0, 0

    async def rerank(
        self, query: str, candidates: list[Candidate], lookup: dict
    ) -> tuple[list[Candidate], dict]:
        if not candidates:
            return [], {"skipped": "no_candidates"}
        texts = [
            "\n".join(
                (
                    lookup[str(c.chunk_id)]["title"],
                    lookup[str(c.chunk_id)]["section_path"],
                    lookup[str(c.chunk_id)]["text"],
                )
            )
            for c in candidates
        ]
        key = digest(
            {
                "revision": self.revision,
                "query": query,
                "ids": [str(c.chunk_id) for c in candidates],
                "texts": texts,
            }
        )
        if key in self.values:
            value = self.values[key]
            self.cache_hits += 1
        else:
            try:
                response = await self.client.post(
                    self.url,
                    json={
                        "model": RERANK_MODEL,
                        "input": {"query": query, "documents": texts},
                        "parameters": {"top_n": len(texts), "instruct": INSTRUCTION},
                    },
                )
            except httpx.HTTPError:
                raise LiteratureError("Reranker network failure (details redacted)") from None
            if response.status_code != 200:
                raise LiteratureError(f"Reranker HTTP {response.status_code} (body redacted)")
            try:
                body = response.json()
                rows = validate_rerank_results(body["output"]["results"], len(candidates))
                usage = body.get("usage", {})
                value = {"results": rows, "request_id": body.get("request_id"), "usage": usage}
            except (KeyError, TypeError, ValueError):
                raise LiteratureError("Malformed reranker response (body redacted)") from None
            self.calls += 1
            self.tokens += int(usage.get("total_tokens", 0))
            self.values[key] = value
            write_json(
                self.path,
                {
                    "domain": DOMAIN,
                    "experiment_id": EXPERIMENT,
                    "revision": self.revision,
                    "values": self.values,
                },
            )
        rows = validate_rerank_results(value["results"], len(candidates))
        return [replace(candidates[r["index"]], score=r["relevance_score"]) for r in rows], {
            "cache_key": key,
            "request_id": value.get("request_id"),
            "usage": value.get("usage", {}),
        }

    async def close(self):
        await self.client.aclose()


async def index_v2(client, collection, chunks, vectors, fingerprint, revision, dimension):
    if not collection.startswith("rag_eval_medical_rehab_v2_"):
        raise LiteratureError("Medical v2 collection required")
    expected = {
        c["chunk_id"]: point_payload(c, fingerprint, revision) | {"experiment_id": EXPERIMENT}
        for c in chunks
    }
    if not await client.collection_exists(collection):
        await client.create_collection(
            collection,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )
    config = (await client.get_collection(collection)).config.params.vectors
    if (
        not isinstance(config, models.VectorParams)
        or config.size != dimension
        or config.distance != models.Distance.COSINE
    ):
        raise LiteratureError("Medical v2 vector configuration differs")
    present = await verify_index(client, collection, expected, complete=False)
    pending = [
        models.PointStruct(id=c["chunk_id"], vector=v, payload=expected[c["chunk_id"]])
        for c, v in zip(chunks, vectors, strict=True)
        if c["chunk_id"] not in present
    ]
    for start in range(0, len(pending), 100):
        await client.upsert(collection, points=pending[start : start + 100], wait=True)
    await verify_index(client, collection, expected, complete=True)
    return expected


async def search_v2(client, collection, vector, expected, fingerprint, revision, conditions=None):
    values = {
        "domain": DOMAIN,
        "experiment_id": EXPERIMENT,
        "corpus_fingerprint": fingerprint,
        "embedding_revision": revision,
        **(conditions or {}),
    }
    response = await client.query_points(
        collection,
        query=vector,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(key=k, match=models.MatchValue(value=v))
                for k, v in values.items()
            ]
        ),
        limit=20,
        with_payload=True,
    )
    result = []
    for rank, p in enumerate(response.points, 1):
        if expected.get(str(p.id)) != p.payload:
            raise LiteratureError("Medical v2 payload/source hydration failed")
        result.append(Candidate(UUID(str(p.id)), p.score, dense_rank=rank, dense_score=p.score))
    return result


def aggregate(rows: list[dict]) -> dict:
    result = {}
    for name in VARIANTS:

        def mean(subset, name=name):
            metrics = [r["variants"][name]["metrics"] for r in subset if r["scored"]]
            return {
                "scored_count": len(metrics),
                **(
                    {k: sum(m[k] for m in metrics) / len(metrics) for k in metrics[0]}
                    if metrics
                    else {}
                ),
            }

        result[name] = mean(rows)
        result[name]["per_category"] = {
            c: mean([r for r in rows if r["category"] == c])
            for c in sorted({r["category"] for r in rows})
        }
    return result


def select_variant(summary: dict) -> str:
    return max(
        VARIANTS,
        key=lambda n: (
            summary[n]["complete_evidence_at_5"],
            summary[n]["required_evidence_coverage"],
            summary[n]["mrr_at_5"],
            -VARIANTS.index(n),
        ),
    )


def validate_selection(selection, dev, gold, revision, config):
    required = {
        "status": "complete",
        "domain": DOMAIN,
        "experiment_id": EXPERIMENT,
        "split": "dev",
        "corpus_fingerprint": gold.corpus_fingerprint,
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "embedding_revision": revision,
        "config": config,
    }
    if any(dev.get(k) != v for k, v in required.items()):
        raise LiteratureError("Development configuration differs; test remains sealed")
    if selection != {
        "dev_report_fingerprint": digest(dev),
        "variant": select_variant(dev["summary"]),
        "config_fingerprint": digest(config),
        "test_seal": gold.test_seal,
    }:
        raise LiteratureError("Selection differs from the frozen development report")


async def run_v2(
    *, root, suite, gold, settings, adapter, client, reranker, split, revision, aliases
):
    if split not in {"dev", "test"}:
        raise LiteratureError("Medical v2 requires dev or test split")
    corpus = root / ".local/medical_rehab_v1/corpus/manifest.json"
    manifest, pages, chunks = load_corpus(corpus, root / "doc")
    if suite.experiment_id != EXPERIMENT or gold.experiment_id != EXPERIMENT:
        raise LiteratureError("Medical v2 suite and Gold required")
    bindings = validate_gold(gold, suite, manifest, pages, chunks)
    lookup = {c["chunk_id"]: c for c in chunks}
    if set(aliases) != {c["document_id"] for c in chunks}:
        raise LiteratureError("Source aliases must cover exactly the approved PDF corpus")
    config = CONFIG | {
        "source_aliases_fingerprint": digest(aliases),
        "rerank_revision": reranker.revision,
    }
    base = root / ".local" / EXPERIMENT
    if split == "test":
        dev = json.loads((base / "results/dev/latest.json").read_text(encoding="utf8"))
        selection = json.loads((base / "selection.json").read_text(encoding="utf8"))
        validate_selection(selection, dev, gold, revision, config)
        if (base / "results/test/latest.json").exists():
            raise LiteratureError("Holdout already evaluated; do not tune or repeatedly open it")
    started = time.perf_counter()
    cache = base / "embeddings"
    # Reuse only validated identical document/query inputs; never write into v1.
    for kind in ("documents", "queries"):
        destination = cache / f"{kind}_{revision}.json"
        original = root / f".local/medical_rehab_v1/embeddings/{kind}_{revision}.json"
        if not destination.exists() and original.exists():
            old = EmbeddingCache(original, revision, settings.embedding_dimension)
            new = EmbeddingCache(destination, revision, settings.embedding_dimension)
            new.values = dict(old.values)
            new.save()
    docs_cache = EmbeddingCache(
        cache / f"documents_{revision}.json", revision, settings.embedding_dimension
    )
    queries = EmbeddingCache(
        cache / f"queries_{revision}.json", revision, settings.embedding_dimension
    )
    texts = ["\n".join((c["title"], c["section_path"], c["text"])) for c in chunks]
    vectors, doc_calls = await docs_cache.documents(adapter, texts)
    load_corpus(corpus, root / "doc")
    fingerprint = manifest["corpus_fingerprint"]
    collection = (
        "rag_eval_medical_rehab_v2_" + digest({"corpus": fingerprint, "embedding": revision})[:16]
    )
    expected = await index_v2(
        client, collection, chunks, vectors, fingerprint, revision, settings.embedding_dimension
    )
    sparse = LocalBM25Retriever(
        {UUID(c["chunk_id"]): text for c, text in zip(chunks, texts, strict=True)}
    )
    rows, query_calls = [], 0
    gold_map = {c.id: c for c in gold.cases}
    for case in suite.cases:
        if case.split != split:
            continue
        before = time.perf_counter()
        vector, calls = await queries.query(adapter, case.query)
        query_calls += calls
        dense = await search_v2(client, collection, vector, expected, fingerprint, revision)
        sparse_all = await sparse.search(case.query, limit=len(chunks))
        hybrid = reciprocal_rank_fusion(dense, sparse_all[:20], k=60)
        sources, content_query = resolve_sources(case.query, aliases)
        routed = hybrid
        if sources:
            content_vector, calls = await queries.query(adapter, content_query)
            query_calls += calls
            content_sparse = await sparse.search(content_query, limit=len(chunks))
            routed = []
            for doc in sources:
                conditions = case.filters.model_dump(exclude_none=True) | {"document_id": doc}
                source_dense = await search_v2(
                    client, collection, content_vector, expected, fingerprint, revision, conditions
                )
                source_sparse = [
                    c
                    for c in content_sparse
                    if lookup[str(c.chunk_id)]["document_id"] == doc
                    and case.filters.matches(lookup[str(c.chunk_id)]["metadata"])
                ][:20]
                routed.extend(reciprocal_rank_fusion(source_dense, source_sparse, k=60))
            routed = balanced(routed, sources, lookup)
        ranked, rerank_audit = await reranker.rerank(case.query, hybrid, lookup)
        route_ranked, route_audit = await reranker.rerank(case.query, routed, lookup)
        variants = {
            "dense": dense,
            "bm25": sparse_all[:20],
            "hybrid": hybrid,
            "hybrid_dedup": deduplicate(hybrid, lookup),
            "source_routed": routed,
            "reranked_hybrid": ranked,
            "source_routed_rerank": balanced(route_ranked, sources, lookup),
        }
        outputs = {}
        for name, candidates in variants.items():
            ids = [str(c.chunk_id) for c in candidates[:5]]
            outputs[name] = {
                "metrics": case_metrics(ids, bindings[case.id])
                if gold_map[case.id].scored
                else None,
                "candidate_ids": [str(c.chunk_id) for c in candidates],
                "candidates": [
                    {
                        "chunk_id": str(c.chunk_id),
                        "score": c.score,
                        "dense_rank": c.dense_rank,
                        "sparse_rank": c.sparse_rank,
                    }
                    for c in candidates
                ],
                "top5": [evidence_card(c, lookup[str(c.chunk_id)]) for c in candidates[:5]],
                "required_group_hits": [bool(set(ids) & g) for g in bindings[case.id]],
                "candidate_group_hits": [
                    bool({str(c.chunk_id) for c in candidates} & g) for g in bindings[case.id]
                ],
            }
        rows.append(
            {
                "id": case.id,
                "query": case.query,
                "category": case.category,
                "split": split,
                "scored": gold_map[case.id].scored,
                "answerable": gold_map[case.id].answerable,
                "expected_behavior": gold_map[case.id].expected_behavior,
                "filters": case.filters.model_dump(exclude_none=True),
                "resolved_sources": sources,
                "content_query": content_query,
                "elapsed_seconds": time.perf_counter() - before,
                "rerank_audit": {"hybrid": rerank_audit, "routed": route_audit},
                "variants": outputs,
            }
        )
        print(f"Medical v2 {split}: {case.id} complete", flush=True)
    load_corpus(corpus, root / "doc")
    await verify_index(client, collection, expected, complete=True)
    summary = aggregate(rows)
    return {
        "schema_version": 1,
        "domain": DOMAIN,
        "experiment_id": EXPERIMENT,
        "status": "complete",
        "split": split,
        "created_at": datetime.now(UTC).isoformat(),
        "corpus_fingerprint": fingerprint,
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "embedding_revision": revision,
        "embedding_model": settings.embedding_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_dimension": settings.embedding_dimension,
        "rerank_model": RERANK_MODEL,
        "collection": collection,
        "backend": "qdrant_local",
        "document_count": len(manifest["documents"]),
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "config": config,
        "retrieval_config_fingerprint": digest(config),
        "model_calls": {
            "document_batches": doc_calls,
            "queries": query_calls,
            "rerank": reranker.calls,
            "rerank_cache_hits": reranker.cache_hits,
            "rerank_total_tokens": reranker.tokens,
        },
        "elapsed_seconds": time.perf_counter() - started,
        "summary": summary,
        "cases": rows,
        "page_mapping_errors": 0,
        "answer_generation_evaluated": False,
        "clinical_approval": False,
        "limitations": [
            "Technical source-text Gold; no independent clinical review",
            "V1 questions are development only; v2 is not numerically comparable to v1",
            "New holdout is authored by the same agent on the same PDF corpus",
            "No patient measurement Gold, answerability threshold or generated-answer score",
            "Page layout sampled; D173 PDF3 remains quarantined",
        ],
    }
