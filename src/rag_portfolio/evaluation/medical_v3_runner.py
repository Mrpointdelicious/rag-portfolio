"""Isolated real-provider ablations, with archived v2 rankings rescored on v3 Gold."""

import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from qdrant_client import models

from rag_portfolio.evaluation.medical_corpus import digest, load_corpus
from rag_portfolio.evaluation.medical_runner import (
    EmbeddingCache,
    evidence_card,
    point_payload,
    verify_index,
)
from rag_portfolio.evaluation.medical_schema import validate_gold
from rag_portfolio.evaluation.medical_v2 import (
    INSTRUCTION,
    RERANK_MODEL,
    balanced,
    resolve_sources,
    validate_rerank_results,
)
from rag_portfolio.evaluation.medical_v3 import (
    CONFIG,
    DOMAIN,
    EXPERIMENT,
    VARIANTS,
    ContextPacker,
    aggregate,
    budget_metrics,
    coverage_order,
    finite_scores,
    group_hits,
    input_action,
    metrics,
    plan_query,
    round_robin_pool,
    validate_selection,
)
from rag_portfolio.evaluation.retrievers import (
    Candidate,
    LocalBM25Retriever,
    reciprocal_rank_fusion,
)
from rag_portfolio.literature_backup import LiteratureError, file_hash, write_json


class RerankerV3:
    def __init__(self, settings, path: Path, *, transport=None, inherit: Path | None = None):
        if (
            settings.embedding_provider != "alibaba_dashscope"
            or not settings.embedding_base_url.rstrip("/").endswith("/api/v1")
        ):
            raise LiteratureError("Native rerank requires the existing DashScope embedding origin")
        self.revision = digest(
            {
                "model": RERANK_MODEL,
                "base_url": settings.embedding_base_url,
                "instruction": INSTRUCTION,
                "input": "title-section-text-v1",
            }
        )
        self.path, self.values = path, {}
        source = path if path.exists() else inherit
        if source is not None and source.exists():
            cache = json.loads(source.read_text(encoding="utf8"))
            expected_experiment = EXPERIMENT if source == path else "medical_rehab_v2"
            if (
                cache.get("domain") != DOMAIN
                or cache.get("experiment_id") != expected_experiment
                or cache.get("revision") != self.revision
            ):
                raise LiteratureError("Foreign or changed medical rerank cache")
            self.values = dict(cache["values"])
        self.url = (
            settings.embedding_base_url.rstrip("/") + "/services/rerank/text-rerank/text-rerank"
        )
        self.client = httpx.AsyncClient(
            timeout=60,
            transport=transport,
            headers={"Authorization": "Bearer " + settings.embedding_api_key.get_secret_value()},
        )
        self.calls = self.cache_hits = self.tokens = 0

    async def rerank(self, query, candidates, lookup):
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
                results = validate_rerank_results(body["output"]["results"], len(candidates))
                usage = body.get("usage", {})
                tokens = int(usage.get("total_tokens", 0))
                if tokens < 0:
                    raise ValueError("negative usage")
                value = {"results": results, "request_id": body.get("request_id"), "usage": usage}
            except (ValueError, TypeError, KeyError, AttributeError):
                raise LiteratureError("Malformed reranker response (body redacted)") from None
            self.calls += 1
            self.tokens += tokens
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
        results = validate_rerank_results(value["results"], len(candidates))
        return [replace(candidates[r["index"]], score=r["relevance_score"]) for r in results], {
            "cache_key": key,
            "request_id": value.get("request_id"),
            "usage": value.get("usage", {}),
        }

    async def close(self):
        await self.client.aclose()


async def index_v3(client, collection, chunks, vectors, fingerprint, revision, dimension):
    if not collection.startswith("rag_eval_medical_rehab_v3_"):
        raise LiteratureError("Medical v3 collection required")
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
        raise LiteratureError("Medical v3 vector configuration differs")
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


async def search(client, collection, vector, expected, fingerprint, revision, conditions=None):
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
    for rank, point in enumerate(response.points, 1):
        if expected.get(str(point.id)) != point.payload:
            raise LiteratureError("Medical v3 payload/source hydration failed")
        result.append(
            Candidate(UUID(str(point.id)), point.score, dense_rank=rank, dense_score=point.score)
        )
    return result


def archives(root, lookup, fingerprint, revision):
    result, hashes = {}, {}
    for split in ("dev", "test"):
        path = root / f".local/medical_rehab_v2/results/{split}/latest.json"
        report = json.loads(path.read_text(encoding="utf8"))
        if (
            report.get("domain") != DOMAIN
            or report.get("experiment_id") != "medical_rehab_v2"
            or report.get("status") != "complete"
            or report.get("corpus_fingerprint") != fingerprint
            or report.get("embedding_revision") != revision
        ):
            raise LiteratureError("Baseline archive is foreign or incomplete")
        hashes[split] = file_hash(path)
        for row in report["cases"]:
            variants = {}
            for name, original in (
                ("hybrid", "hybrid"),
                ("reranked_hybrid", "reranked_hybrid"),
                ("v2_source_routed_rerank", "source_routed_rerank"),
            ):
                records = row["variants"][original]["candidates"]
                candidates = [
                    Candidate(
                        UUID(c["chunk_id"]),
                        c["score"],
                        dense_rank=c.get("dense_rank"),
                        sparse_rank=c.get("sparse_rank"),
                    )
                    for c in records
                ]
                finite_scores(candidates)
                if any(str(c.chunk_id) not in lookup for c in candidates):
                    raise LiteratureError("Foreign baseline candidate")
                variants[name] = candidates
            # Only query and real rankings enter the replay retriever, no answer annotations.
            result[row["query"]] = variants
    return result, hashes


async def run(*, root, suite, gold, settings, adapter, client, reranker, revision, split):
    if (
        split not in {"dev", "test"}
        or suite.experiment_id != EXPERIMENT
        or gold.experiment_id != EXPERIMENT
    ):
        raise LiteratureError("Explicit medical v3 suite/split required")
    corpus = root / ".local/medical_rehab_v1/corpus/manifest.json"
    manifest, pages, chunks = load_corpus(corpus, root / "doc")
    validate_gold(gold, suite, manifest, pages, chunks)
    public, base = root / "eval" / EXPERIMENT, root / ".local" / EXPERIMENT
    aliases = json.loads((public / "source_aliases.json").read_text(encoding="utf8"))
    policy = json.loads((public / "review_policy.json").read_text(encoding="utf8"))
    if gold.review_policy_fingerprint != digest(policy):
        raise LiteratureError("Review policy changed after sealing")
    lookup = {c["chunk_id"]: c for c in chunks}
    documents = {c["document_id"]: {"title": c["title"], "metadata": c["metadata"]} for c in chunks}
    if set(aliases) != set(documents):
        raise LiteratureError("Aliases must match the frozen PDF corpus")
    replay, archive_hashes = archives(root, lookup, manifest["corpus_fingerprint"], revision)
    config = CONFIG | {
        "implementation_fingerprint": digest(
            {
                "core": file_hash(Path(__file__).with_name("medical_v3.py")),
                "runner": file_hash(Path(__file__)),
            }
        ),
        "aliases_fingerprint": digest(aliases),
        "review_policy_fingerprint": digest(policy),
        "rerank_model": RERANK_MODEL,
        "rerank_revision": reranker.revision,
        "archive_hashes": archive_hashes,
    }
    sealed = json.loads((base / "freeze_record.json").read_text(encoding="utf8"))
    if (
        sealed["test_seal"] != gold.test_seal
        or sealed["suite_fingerprint"] != gold.suite_fingerprint
    ):
        raise LiteratureError("V3 pre-run freeze record changed")
    if split == "test":
        dev = json.loads((base / "results/dev/latest.json").read_text(encoding="utf8"))
        selection = json.loads((base / "selection.json").read_text(encoding="utf8"))
        validate_selection(selection, dev, gold, revision, config)
        if (base / "results/test/latest.json").exists():
            raise LiteratureError("V3 holdout already evaluated; new tuning needs a new revision")
    if split == "dev" and (base / "selection.json").exists():
        raise LiteratureError("V3 development selection already frozen")
    started = time.perf_counter()
    cache_root = base / "embeddings"
    for kind in ("documents", "queries"):
        destination = cache_root / f"{kind}_{revision}.json"
        original = root / f".local/medical_rehab_v2/embeddings/{kind}_{revision}.json"
        if not destination.exists():
            old = EmbeddingCache(original, revision, settings.embedding_dimension)
            new = EmbeddingCache(destination, revision, settings.embedding_dimension)
            new.values = dict(old.values)
            new.save()
    doc_cache = EmbeddingCache(
        cache_root / f"documents_{revision}.json", revision, settings.embedding_dimension
    )
    query_cache = EmbeddingCache(
        cache_root / f"queries_{revision}.json", revision, settings.embedding_dimension
    )
    texts = ["\n".join((c["title"], c["section_path"], c["text"])) for c in chunks]
    vectors, doc_calls = await doc_cache.documents(adapter, texts)
    load_corpus(corpus, root / "doc")
    fingerprint = manifest["corpus_fingerprint"]
    collection = (
        "rag_eval_medical_rehab_v3_" + digest({"corpus": fingerprint, "embedding": revision})[:16]
    )
    expected = await index_v3(
        client, collection, chunks, vectors, fingerprint, revision, settings.embedding_dimension
    )
    sparse = LocalBM25Retriever(
        {UUID(c["chunk_id"]): text for c, text in zip(chunks, texts, strict=True)}
    )
    packer = ContextPacker(pages, lookup)
    rows, query_calls = [], 0
    gold_map = {c.id: c for c in gold.cases}

    async def retrieve(query, conditions=None):
        nonlocal query_calls
        vector, calls = await query_cache.query(adapter, query)
        query_calls += calls
        dense = await search(
            client, collection, vector, expected, fingerprint, revision, conditions
        )
        all_sparse = await sparse.search(query, limit=len(chunks))
        filtered = [
            c
            for c in all_sparse
            if all(
                value in lookup[str(c.chunk_id)]["metadata"].get(key, [])
                if isinstance(lookup[str(c.chunk_id)]["metadata"].get(key), list)
                else (
                    lookup[str(c.chunk_id)]["document_id"]
                    if key == "document_id"
                    else lookup[str(c.chunk_id)]["metadata"].get(key)
                )
                == value
                for key, value in (conditions or {}).items()
            )
        ][:20]
        return reciprocal_rank_fusion(dense, filtered, k=60)

    for case in suite.cases:
        if case.split != split:
            continue
        before = time.perf_counter()
        action = input_action(case.query, aliases)
        blocked = action not in {
            "retrieve_literature",
            "literature_with_patient_limit",
            "provenance_only",
        }
        sources, tasks = plan_query(case.query, aliases, documents)
        calls_before, queries_before = reranker.calls, query_calls
        audits, timings = {}, {}
        if case.query in replay:
            baselines = replay[case.query]
            replayed = True
        elif blocked:
            baselines = {n: [] for n in VARIANTS[:3]}
            replayed = False
        else:
            baseline_start = time.perf_counter()
            hybrid = await retrieve(case.query)
            ranked, audit = await reranker.rerank(case.query, hybrid, lookup)
            audits["baseline"] = audit
            old_sources, stripped = resolve_sources(case.query, aliases)
            routed = []
            for doc in old_sources:
                routed.extend(
                    await retrieve(
                        stripped, case.filters.model_dump(exclude_none=True) | {"document_id": doc}
                    )
                )
            routed = balanced(routed, old_sources, lookup) if old_sources else hybrid
            routed_ranked, audit = await reranker.rerank(case.query, routed, lookup)
            audits["old_route"] = audit
            baselines = {
                "hybrid": hybrid,
                "reranked_hybrid": ranked,
                "v2_source_routed_rerank": balanced(routed_ranked, old_sources, lookup),
            }
            timings["baseline_live"] = time.perf_counter() - baseline_start
            replayed = False
        if blocked:
            preserved = decomposed = selected = []
            task_rankings, choice_audit = {}, []
        else:
            stage = time.perf_counter()
            # Keep the whole original query and its global recall as a fallback.
            global_hybrid = baselines["hybrid"]
            source_pools = [global_hybrid]
            for doc in sources:
                source_pools.append(
                    await retrieve(
                        case.query,
                        case.filters.model_dump(exclude_none=True) | {"document_id": doc},
                    )
                )
            preserved_pool = round_robin_pool(source_pools)
            preserved, audits["preserved"] = await reranker.rerank(
                case.query, preserved_pool, lookup
            )
            timings["preserved"] = time.perf_counter() - stage
            stage = time.perf_counter()
            task_rankings = {}
            for task in tasks:
                conditions = case.filters.model_dump(exclude_none=True)
                if task.document_id is not None:
                    conditions["document_id"] = task.document_id
                task_rankings[task.id] = await retrieve(task.query, conditions)
            decomposed_pool = round_robin_pool([preserved_pool, *task_rankings.values()])
            decomposed, audits["decomposed"] = await reranker.rerank(
                case.query, decomposed_pool, lookup
            )
            timings["decomposed"] = time.perf_counter() - stage
            stage = time.perf_counter()
            selected, choice_audit = coverage_order(decomposed, task_rankings)
            timings["coverage_selection"] = time.perf_counter() - stage
        variants = baselines | {
            "preserved_query": preserved,
            "decomposed": decomposed,
            "coverage": selected,
            "context_expanded": selected,
        }
        outputs = {}
        for name, candidates in variants.items():
            finite_scores(candidates)
            cards = [evidence_card(c, lookup[str(c.chunk_id)]) for c in candidates[:5]]
            all_cards = [evidence_card(c, lookup[str(c.chunk_id)]) for c in candidates]
            pack = packer.pack(candidates, expand=name == "context_expanded")
            outputs[name] = {
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
                "top5": cards,
                "metrics": metrics(cards, gold_map[case.id]),
                "required_group_hits": group_hits(cards, gold_map[case.id]),
                "candidate_group_hits": group_hits(all_cards, gold_map[case.id]),
                "context_pack": pack,
                "budget_metrics": budget_metrics(pack["cards"], gold_map[case.id]),
                "budget_group_hits": group_hits(pack["cards"], gold_map[case.id]),
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
                "expected_action": case.expected_action,
                "input_action": action,
                "action_correct": action == case.expected_action,
                "patient_value_generated": False,
                "filters": case.filters.model_dump(exclude_none=True),
                "resolved_sources": sources,
                "tasks": [asdict(t) for t in tasks],
                "task_rankings": {
                    key: [{"chunk_id": str(c.chunk_id), "score": c.score} for c in value]
                    for key, value in task_rankings.items()
                },
                "coverage_selection_audit": choice_audit,
                "baseline_replayed": replayed,
                "stage_seconds": timings,
                "model_calls": {
                    "query_embeddings": query_calls - queries_before,
                    "rerank": reranker.calls - calls_before,
                },
                "elapsed_seconds": time.perf_counter() - before,
                "rerank_audit": audits,
                "variants": outputs,
            }
        )
        print(f"Medical v3 {split}: {case.id} complete", flush=True)
    load_corpus(corpus, root / "doc")
    await verify_index(client, collection, expected, complete=True)
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
        "summary": aggregate(rows),
        "input_action_summary": {
            "count": len(rows),
            "correct": sum(r["action_correct"] for r in rows),
            "patient_values_generated": 0,
            "evaluation_scope": "deterministic input routing only; no generated-answer evaluation",
        },
        "cases": rows,
        "answer_generation_evaluated": False,
        "clinical_approval": False,
        "limitations": [
            "Same-agent technical source-fact review, no independent clinical approval",
            "New holdout is from the same corpus; some facts recur across questions",
            "V2 replay is for exposed questions only; no replay latency comparison",
            "cl100k_base is a fixed comparison tokenizer, not the Qwen billing tokenizer",
            "Only query input routing is evaluated, not a clinical answer or refusal model",
            "Original PDFs and chunk index unchanged; context expansion uses verified pages",
        ],
    }
