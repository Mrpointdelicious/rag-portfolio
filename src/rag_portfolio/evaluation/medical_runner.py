"""Isolated medical Dense/BM25/RRF experiment; no production review or release writes."""

import math
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from qdrant_client import AsyncQdrantClient, models

from rag_portfolio.evaluation.experiments import MEDICAL_DOMAIN, MEDICAL_EXPERIMENT
from rag_portfolio.evaluation.medical_corpus import digest, load_corpus, text_digest
from rag_portfolio.evaluation.medical_schema import (
    MedicalGold,
    MedicalSuite,
    case_metrics,
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

CANDIDATE_DEPTH = 20
FINAL_DEPTH = 5
RRF_K = 60
BATCH_SIZE = 20
VARIANTS = ("dense", "bm25", "hybrid", "filtered_hybrid")
RETRIEVAL_CONFIG = {
    "candidate_depth": CANDIDATE_DEPTH,
    "final_depth": FINAL_DEPTH,
    "rrf_k": RRF_K,
    "bm25_k1": BM25_K1,
    "bm25_b": BM25_B,
    "tokenizer_version": TOKENIZER_VERSION,
    "embedding_input": "title-section-text-v1",
}


def embedding_revision(settings) -> str:
    return digest(
        {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "base_url": settings.embedding_base_url,
            "dimension": settings.embedding_dimension,
            "output_type": settings.embedding_output_type,
            "input": "title-section-text-v1",
        }
    )


class EmbeddingCache:
    def __init__(self, path: Path, revision: str, dimension: int):
        import json

        self.path, self.revision, self.dimension = path, revision, dimension
        self.values = {}
        if path.exists():
            content = json.loads(path.read_text(encoding="utf-8"))
            if (
                content.get("domain") != MEDICAL_DOMAIN
                or content.get("revision") != revision
                or content.get("dimension") != dimension
            ):
                raise LiteratureError("Embedding cache belongs to another domain/model")
            self.values = content["vectors"]
            for vector in self.values.values():
                self._validate(vector)

    def _validate(self, vector: list[float]) -> None:
        if (
            not isinstance(vector, list)
            or len(vector) != self.dimension
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)
        ):
            raise LiteratureError("Invalid embedding cache vector")
        if not any(vector):
            raise LiteratureError("Zero embedding vector")

    def save(self) -> None:
        write_json(
            self.path,
            {
                "domain": MEDICAL_DOMAIN,
                "revision": self.revision,
                "dimension": self.dimension,
                "vectors": self.values,
            },
        )

    async def documents(
        self, adapter, texts: list[str], progress=print
    ) -> tuple[list[list[float]], int]:
        pending = list(
            dict.fromkeys(
                text for text in texts if text_digest("document:" + text) not in self.values
            )
        )
        calls = 0
        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            vectors = await adapter.embed_documents(batch)
            if len(vectors) != len(batch):
                raise LiteratureError("Embedding batch count differs")
            for text, vector in zip(batch, vectors, strict=True):
                self._validate(vector)
                self.values[text_digest("document:" + text)] = vector
            self.save()
            calls += 1
            progress(
                "Medical document embeddings: "
                f"{min(start + BATCH_SIZE, len(pending))}/{len(pending)}",
                flush=True,
            )
        return [self.values[text_digest("document:" + text)] for text in texts], calls

    async def query(self, adapter, text: str) -> tuple[list[float], int]:
        key = text_digest("query:" + text)
        if key in self.values:
            return self.values[key], 0
        vector = await adapter.embed_query(text)
        self._validate(vector)
        self.values[key] = vector
        self.save()
        return vector, 1


def point_payload(chunk: dict, corpus_fingerprint: str, revision: str) -> dict:
    return {
        "domain": MEDICAL_DOMAIN,
        "experiment_id": MEDICAL_EXPERIMENT,
        "corpus_fingerprint": corpus_fingerprint,
        "embedding_revision": revision,
        "chunk_id": chunk["chunk_id"],
        "document_id": chunk["document_id"],
        "source_sha256": chunk["source_sha256"],
        "text_sha256": chunk["text_sha256"],
        "pdf_page_start": chunk["pdf_page_start"],
        "pdf_page_end": chunk["pdf_page_end"],
        **{
            key: chunk["metadata"].get(key)
            for key in ("disease", "population", "stage", "literature_type", "edition")
        },
    }


async def verify_index(
    client: AsyncQdrantClient, collection: str, expected: dict[str, dict], *, complete: bool
) -> set[str]:
    found = set()
    offset = None
    while True:
        points, offset = await client.scroll(
            collection, offset=offset, limit=256, with_payload=True, with_vectors=False
        )
        for point in points:
            key = str(point.id)
            if key not in expected or point.payload != expected[key]:
                raise LiteratureError("Foreign, stale or changed point in medical index")
            found.add(key)
        if offset is None:
            break
    if complete and found != expected.keys():
        raise LiteratureError("Medical index is incomplete")
    return found


async def index_corpus(
    client,
    collection: str,
    chunks: list[dict],
    vectors: list[list[float]],
    fingerprint: str,
    revision: str,
    dimension: int,
) -> dict[str, dict]:
    if not collection.startswith("rag_eval_medical_rehab_v1_"):
        raise LiteratureError("Medical collection namespace required")
    expected = {chunk["chunk_id"]: point_payload(chunk, fingerprint, revision) for chunk in chunks}
    if not await client.collection_exists(collection):
        await client.create_collection(
            collection,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )
    info = await client.get_collection(collection)
    config = info.config.params.vectors
    if (
        not isinstance(config, models.VectorParams)
        or config.size != dimension
        or config.distance != models.Distance.COSINE
    ):
        raise LiteratureError("Medical collection vector configuration differs")
    present = await verify_index(client, collection, expected, complete=False)
    pending = [
        models.PointStruct(id=chunk["chunk_id"], vector=vector, payload=expected[chunk["chunk_id"]])
        for chunk, vector in zip(chunks, vectors, strict=True)
        if chunk["chunk_id"] not in present
    ]
    for start in range(0, len(pending), 100):
        await client.upsert(collection, points=pending[start : start + 100], wait=True)
    await verify_index(client, collection, expected, complete=True)
    return expected


def query_filter(fingerprint: str, revision: str, conditions: dict | None = None) -> models.Filter:
    values = {
        "domain": MEDICAL_DOMAIN,
        "experiment_id": MEDICAL_EXPERIMENT,
        "corpus_fingerprint": fingerprint,
        "embedding_revision": revision,
        **(conditions or {}),
    }
    return models.Filter(
        must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in values.items()
        ]
    )


async def dense_search(
    client,
    collection: str,
    vector: list[float],
    fingerprint: str,
    revision: str,
    expected: dict,
    conditions: dict | None = None,
) -> list[Candidate]:
    result = await client.query_points(
        collection,
        query=vector,
        query_filter=query_filter(fingerprint, revision, conditions),
        limit=CANDIDATE_DEPTH,
        with_payload=True,
        with_vectors=False,
    )
    candidates = []
    for rank, point in enumerate(result.points, 1):
        key = str(point.id)
        if key not in expected or point.payload != expected[key]:
            raise LiteratureError("Medical evidence failed payload/source hydration checks")
        candidates.append(
            Candidate(UUID(key), point.score, dense_rank=rank, dense_score=point.score)
        )
    return candidates


def evidence_card(candidate: Candidate, chunk: dict) -> dict:
    return {
        "chunk_id": chunk["chunk_id"],
        "document_id": chunk["document_id"],
        "title": chunk["title"],
        "edition": chunk["metadata"].get("edition"),
        "publication_year": chunk["metadata"].get("publication_year"),
        "source_path": chunk["source_path"],
        "source_sha256": chunk["source_sha256"],
        "pdf_page_start": chunk["pdf_page_start"],
        "pdf_page_end": chunk["pdf_page_end"],
        "printed_page": chunk["printed_page"],
        "section": chunk["section_path"],
        "start_char": chunk["start_char"],
        "end_char": chunk["end_char"],
        "text": chunk["text"],
        "score": candidate.score,
        "dense_rank": candidate.dense_rank,
        "sparse_rank": candidate.sparse_rank,
    }


def validate_test_prerequisites(dev: dict, gold: MedicalGold, revision: str) -> None:
    required = {
        "status": "complete",
        "domain": MEDICAL_DOMAIN,
        "experiment_id": MEDICAL_EXPERIMENT,
        "split": "dev",
        "corpus_fingerprint": gold.corpus_fingerprint,
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "embedding_revision": revision,
        "config": RETRIEVAL_CONFIG,
        "retrieval_config_fingerprint": digest(RETRIEVAL_CONFIG),
    }
    if any(dev.get(key) != value for key, value in required.items()):
        raise LiteratureError("Development configuration differs; test remains sealed")


async def run_medical(
    *,
    suite: MedicalSuite,
    gold: MedicalGold,
    corpus_path: Path,
    backup_root: Path,
    adapter,
    client: AsyncQdrantClient,
    revision: str,
    dimension: int,
    cache_root: Path,
    split: str,
    progress=print,
) -> dict:
    if split not in {"dev", "test"}:
        raise LiteratureError("Run development and sealed test sets separately")
    manifest, pages, chunks = load_corpus(corpus_path, backup_root)
    bindings = validate_gold(gold, suite, manifest, pages, chunks)
    fingerprint = manifest["corpus_fingerprint"]
    collection = (
        "rag_eval_medical_rehab_v1_" + digest({"corpus": fingerprint, "embedding": revision})[:16]
    )
    documents_cache = EmbeddingCache(cache_root / f"documents_{revision}.json", revision, dimension)
    query_cache = EmbeddingCache(cache_root / f"queries_{revision}.json", revision, dimension)
    texts = ["\n".join((chunk["title"], chunk["section_path"], chunk["text"])) for chunk in chunks]
    started = time.perf_counter()
    vectors, document_calls = await documents_cache.documents(adapter, texts, progress)
    # A source change during model calls must prevent publishing an apparently valid run.
    load_corpus(corpus_path, backup_root)
    expected = await index_corpus(
        client, collection, chunks, vectors, fingerprint, revision, dimension
    )
    lookup = {chunk["chunk_id"]: chunk for chunk in chunks}
    sparse = LocalBM25Retriever(
        {
            UUID(key): "\n".join((row["title"], row["section_path"], row["text"]))
            for key, row in lookup.items()
        }
    )
    gold_map = {case.id: case for case in gold.cases}
    rows, query_calls = [], 0
    for case in suite.cases:
        if case.split != split:
            continue
        gold_case = gold_map[case.id]
        before = time.perf_counter()
        vector, called = await query_cache.query(adapter, case.query)
        query_calls += called
        dense = await dense_search(client, collection, vector, fingerprint, revision, expected)
        # Compute BM25 statistics on the same full frozen corpus. Apply eligibility
        # before truncating to 20, rather than filtering a pre-truncated Top-20.
        sparse_all = await sparse.search(case.query, limit=len(chunks))
        sparse_top = sparse_all[:CANDIDATE_DEPTH]
        conditions = case.filters.model_dump(exclude_none=True)
        filtered_dense = (
            await dense_search(
                client, collection, vector, fingerprint, revision, expected, conditions
            )
            if conditions
            else dense
        )
        filtered_sparse = [
            hit for hit in sparse_all if case.filters.matches(lookup[str(hit.chunk_id)]["metadata"])
        ][:CANDIDATE_DEPTH]
        variants = {
            "dense": dense,
            "bm25": sparse_top,
            "hybrid": reciprocal_rank_fusion(dense, sparse_top, k=RRF_K),
            "filtered_hybrid": reciprocal_rank_fusion(filtered_dense, filtered_sparse, k=RRF_K),
        }
        result = {}
        for name, candidates in variants.items():
            chosen = candidates[:FINAL_DEPTH]
            if name == "filtered_hybrid" and any(
                not case.filters.matches(lookup[str(hit.chunk_id)]["metadata"]) for hit in chosen
            ):
                raise LiteratureError("Medical filter violation after hydration")
            ids = [str(hit.chunk_id) for hit in chosen]
            result[name] = {
                "metrics": case_metrics(ids, bindings[case.id]) if gold_case.scored else None,
                "candidate_ids": [str(hit.chunk_id) for hit in candidates],
                "candidates": [
                    {
                        "chunk_id": str(hit.chunk_id),
                        "score": hit.score,
                        "dense_rank": hit.dense_rank,
                        "dense_score": hit.dense_score,
                        "sparse_rank": hit.sparse_rank,
                        "sparse_score": hit.sparse_score,
                        "fused_rank": hit.fused_rank,
                    }
                    for hit in candidates
                ],
                "top5": [evidence_card(hit, lookup[str(hit.chunk_id)]) for hit in chosen],
                "required_group_hits": [bool(set(ids) & group) for group in bindings[case.id]],
            }
        rows.append(
            {
                "id": case.id,
                "query": case.query,
                "category": case.category,
                "split": split,
                "scored": gold_case.scored,
                "answerable": gold_case.answerable,
                "expected_behavior": gold_case.expected_behavior,
                "filters": conditions,
                "elapsed_seconds": time.perf_counter() - before,
                "variants": result,
            }
        )
        progress(f"Medical {split}: {case.id} complete", flush=True)
    load_corpus(corpus_path, backup_root)
    await verify_index(client, collection, expected, complete=True)
    summaries = {}
    for name in VARIANTS:
        metrics = [row["variants"][name]["metrics"] for row in rows if row["scored"]]
        summaries[name] = {
            "scored_count": len(metrics),
            **(
                {key: sum(metric[key] for metric in metrics) / len(metrics) for key in metrics[0]}
                if metrics
                else {}
            ),
        }
        summaries[name]["multi_source"] = {}
        for category in sorted({row["category"] for row in rows}):
            subset = [
                row["variants"][name]["metrics"]
                for row in rows
                if row["scored"] and row["category"] == category
            ]
            summaries[name].setdefault("per_category", {})[category] = {
                "scored_count": len(subset),
                **(
                    {key: sum(item[key] for item in subset) / len(subset) for key in subset[0]}
                    if subset
                    else {}
                ),
            }
        multi = [
            row["variants"][name]["metrics"]
            for row in rows
            if row["scored"] and row["category"] == "多篇证据"
        ]
        summaries[name]["multi_source"] = {
            "count": len(multi),
            **(
                {
                    key: sum(item[key] for item in multi) / len(multi)
                    for key in ("required_evidence_coverage", "complete_evidence_at_5")
                }
                if multi
                else {}
            ),
        }
    return {
        "schema_version": 1,
        "domain": MEDICAL_DOMAIN,
        "experiment_id": MEDICAL_EXPERIMENT,
        "status": "complete",
        "split": split,
        "created_at": datetime.now(UTC).isoformat(),
        "corpus_fingerprint": fingerprint,
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "embedding_revision": revision,
        "collection": collection,
        "backend": "qdrant_local",
        "page_count": len(pages),
        "document_count": len(manifest["documents"]),
        "chunk_count": len(chunks),
        "config": RETRIEVAL_CONFIG,
        "retrieval_config_fingerprint": digest(RETRIEVAL_CONFIG),
        "model_calls": {"document_batches": document_calls, "queries": query_calls},
        "elapsed_seconds": time.perf_counter() - started,
        "summary": summaries,
        "cases": rows,
        "page_mapping_errors": 0,
        "filtered_hydration_violations": 0,
        "answer_generation_evaluated": False,
        "clinical_approval": False,
        "limitations": [
            "Technical source-text Gold reviewed by Codex; no independent clinical approval",
            "No answerability threshold or generated-answer score",
            "Page layout was sampled, not reviewed on all 102 pages",
            "All unknown population/stage values remain unknown",
        ],
    }


def write_report(report: dict, output: Path, *, backup_root: Path | None = None) -> Path:
    if report.get("status") != "complete" or report.get("domain") != MEDICAL_DOMAIN:
        raise LiteratureError("Only complete medical runs may be published")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run = output / "runs" / stamp
    run.mkdir(parents=True, exist_ok=False)
    write_json(run / "report.json", report)
    lines = [
        f"# 医学检索实验 {report['split']}",
        "",
        f"语料：{report['document_count']} 份原始 PDF / "
        f"{report['page_count']} 页 / {report['chunk_count']} 块。",
        "",
        "| 检索方式 | 计分题 | Hit@1 | Hit@5 | MRR@5 | 必需证据组覆盖 | 完整覆盖@5 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in report["summary"].items():
        values = " | ".join(
            f"{metrics.get(key, 0):.4f}"
            for key in (
                "hit_at_1",
                "hit_at_5",
                "mrr_at_5",
                "required_evidence_coverage",
                "complete_evidence_at_5",
            )
        )
        lines.append(f"| {name} | {metrics['scored_count']} | {values} |")
    lines.extend(
        [
            "",
            "这是离线检索实验。无答案题仅观察候选与分数；未评估生成回答或设定生产拒答阈值。",
            "",
            "## 逐题证据卡",
            "",
        ]
    )
    for case in report["cases"]:
        lines.extend(
            [
                f"### {case['id']} {case['query']}",
                "",
                f"预期行为：{case['expected_behavior']}；计分：{case['scored']}。",
                "",
            ]
        )
        for name, variant in case["variants"].items():
            lines.extend([f"#### {name}", "", f"指标：{variant['metrics']}", ""])
            for rank, card in enumerate(variant["top5"], 1):
                title = card["title"]
                if backup_root is not None:
                    source = (backup_root / card["source_path"]).resolve()
                    if not source.is_relative_to((backup_root / "selected").resolve()):
                        raise LiteratureError("Evidence PDF must be in the selected backup folder")
                    title = f"[{title}](<{source.as_posix()}>)"
                lines.extend(
                    [
                        f"{rank}. {title}；版次 {card['edition']}；"
                        f"PDF 第 {card['pdf_page_start']} 页；{card['section']}；"
                        f"字符 {card['start_char']}–{card['end_char']}。",
                        "",
                        "    " + card["text"].replace("\n", "\n    "),
                        "",
                    ]
                )
    (run / "evidence_cards.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(output / "latest.json", report)
    return run
