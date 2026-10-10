"""Fixed multi-document Dense evaluation, multi-Gold metrics and local evidence reports."""

import json
import os
import tempfile
from collections import Counter
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator
from qdrant_client import models

from rag_portfolio.adapters.embedding import EmbeddingAdapter
from rag_portfolio.adapters.vector import QdrantConnection, payload_filter
from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.evaluation.corpus import (
    CorpusSnapshot,
    EvaluationError,
    FrozenModel,
    Nonempty,
    hydrate_frozen_chunks,
    load_frozen_corpus,
)
from rag_portfolio.services.dense_retrieval import (
    EMBEDDING_INPUT_VERSION,
    ApprovedCorpus,
    build_embedding_revision,
    build_embedding_text,
    build_qdrant_payload,
)

COLLECTION = "rag_eval_yueyang_dense_v1"
BATCH_SIZE = 20
TOP_K = 5
DEFAULT_SUITE_PATH = Path("eval/hospital_dense_v1/cases.json")
DEFAULT_OUTPUT_DIR = Path(".local/eval/hospital_dense_v1")
CATEGORY_COUNTS = {
    "exact_fact": 6,
    "semantic_paraphrase": 4,
    "patient_role": 4,
    "doctor_role": 4,
    "role_disambiguation": 4,
    "multi_gold": 2,
    "boundary_negative": 1,
    "no_answer": 3,
    "source_conflict": 1,
}
FAILURE_LABELS = {
    "gold_miss_top5": "Gold not in Top-5",
    "gold_rank_2_5": "Gold rank 2-5",
    "top1_wrong_document": "Top1 wrong document",
    "role_top1_wrong_document": "Role-disambiguation Top1 wrong role document",
}


class GoldRef(FrozenModel):
    external_key: Nonempty
    section_contains: Nonempty
    text_contains: Nonempty | None = None


class EvalCase(FrozenModel):
    id: Nonempty
    query: Nonempty
    category: Literal[
        "exact_fact",
        "semantic_paraphrase",
        "patient_role",
        "doctor_role",
        "role_disambiguation",
        "multi_gold",
        "boundary_negative",
        "no_answer",
        "source_conflict",
    ]
    answerable: StrictBool
    scored: StrictBool
    gold_refs: tuple[GoldRef, ...]

    @model_validator(mode="after")
    def validate_scoring(self) -> "EvalCase":
        if self.category == "no_answer":
            if self.answerable or self.scored or self.gold_refs:
                raise ValueError("No-answer must have no Gold and be excluded from metrics")
        elif self.category == "source_conflict":
            if not self.answerable or self.scored or not self.gold_refs:
                raise ValueError(
                    "Source conflict must expose evidence and be excluded from metrics"
                )
        elif not self.answerable or not self.scored or not self.gold_refs:
            raise ValueError("Quality cases require answerable=true, scored=true and Gold")
        return self


class EvalSuite(FrozenModel):
    suite: Literal["hospital_dense_v1"]
    cases: tuple[EvalCase, ...] = Field(min_length=29, max_length=29)

    @model_validator(mode="after")
    def validate_cases(self) -> "EvalSuite":
        expected_ids = {
            f"{prefix}{index:02d}"
            for prefix, count in (
                ("E", 6),
                ("S", 4),
                ("P", 4),
                ("D", 4),
                ("R", 4),
                ("M", 2),
                ("B", 1),
                ("N", 3),
                ("C", 1),
            )
            for index in range(1, count + 1)
        }
        if {case.id for case in self.cases} != expected_ids:
            raise ValueError("Suite must contain the 29 unique fixed case IDs")
        if Counter(case.category for case in self.cases) != CATEGORY_COUNTS:
            raise ValueError("Suite category counts differ from v1")
        return self


def read_suite(path: Path) -> EvalSuite:
    try:
        return EvalSuite.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvaluationError("Cannot read a valid hospital_dense_v1 suite") from None


async def embed_documents_batched(
    adapter: EmbeddingAdapter, texts: list[str], *, batch_size: int = BATCH_SIZE
) -> list[list[float]]:
    if type(batch_size) is not int or batch_size <= 0 or not texts:
        raise EvaluationError("Batch size must be positive and texts must be nonempty")
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        result = await adapter.embed_documents(batch)
        if len(result) != len(batch):
            raise EvaluationError("Embedding batch count mismatch")
        vectors.extend(result)
    # All batches complete before any collection mutation or upsert.
    return vectors


def resolve_gold(case: EvalCase, corpora: list[ApprovedCorpus]) -> set[UUID]:
    gold = set()
    for ref in case.gold_refs:
        matches = [
            chunk.id
            for corpus in corpora
            if corpus.document.external_key == ref.external_key
            for chunk in corpus.chunks
            if ref.section_contains in chunk.section_path
            and (ref.text_contains is None or ref.text_contains in chunk.text)
        ]
        if len(matches) != 1:
            raise EvaluationError(
                f"{case.id}: Gold ref for {ref.external_key} "
                f"matched {len(matches)} chunks; expected 1"
            )
        gold.add(matches[0])
    return gold


def case_result(
    case: EvalCase, gold_ids: set[UUID], hits: list[dict], gold_audiences: set[str]
) -> dict:
    hits = hits[:TOP_K]
    gold_documents = sorted({ref.external_key for ref in case.gold_refs})
    gold_rank = next((hit["rank"] for hit in hits if UUID(hit["chunk_id"]) in gold_ids), None)
    result = {
        "id": case.id,
        "query": case.query,
        "category": case.category,
        "answerable": case.answerable,
        "scored": case.scored,
        "gold_chunk_ids": sorted(str(value) for value in gold_ids),
        "gold_documents": gold_documents,
        "gold_rank": gold_rank,
        "top5": hits,
        "metrics": None,
        "failures": [],
    }
    if case.answerable and case.scored:
        doc_hit = bool(hits and hits[0]["external_key"] in gold_documents)
        result["metrics"] = {
            **{f"Recall@{k}": int(gold_rank is not None and gold_rank <= k) for k in (1, 3, 5)},
            "MRR@5": 1 / gold_rank if gold_rank is not None else 0.0,
            "DocHit@1": int(doc_hit),
        }
        if gold_rank is None:
            result["failures"].append("gold_miss_top5")
        elif gold_rank > 1:
            result["failures"].append("gold_rank_2_5")
        if not doc_hit:
            result["failures"].append("top1_wrong_document")
            # Evidence role comes from Gold documents, not the questioner's persona:
            # R02 needs the patient-only restriction; R03 needs the doctor-only restriction.
            if (
                case.category == "role_disambiguation"
                and hits
                and (gold_audiences.isdisjoint(hits[0]["audiences"]))
            ):
                result["failures"].append("role_top1_wrong_document")
    else:
        result["excluded_from_quality_metrics"] = True
        result["observation"] = (
            "SOURCE_CONFLICT" if case.category == "source_conflict" else "NO_ANSWER"
        )
        result["top1_score"] = hits[0]["score"] if hits else None
        result["top1_document"] = hits[0]["external_key"] if hits else None
        result["top1_section"] = hits[0]["section_path"] if hits else None
    return result


def aggregate_metrics(results: list[dict]) -> dict:
    scored = [row for row in results if row["answerable"] and row["scored"]]

    def average(rows):
        names = ("Recall@1", "Recall@3", "Recall@5", "MRR@5", "DocHit@1")
        return {
            "count": len(rows),
            **{
                name: sum(row["metrics"][name] for row in rows) / len(rows) if rows else None
                for name in names
            },
        }

    categories = dict.fromkeys(row["category"] for row in scored)
    return {
        "overall": average(scored),
        "per_category": {
            name: average([row for row in scored if row["category"] == name]) for name in categories
        },
        "failures": {
            label: [row["id"] for row in scored if label in row["failures"]]
            for label in FAILURE_LABELS
        },
    }


async def validate_index(
    qdrant: QdrantConnection, points: list[models.PointStruct], *, complete: bool
) -> None:
    expected = {str(point.id): point.payload for point in points}
    seen = set()
    offset = None
    while True:
        records, offset = await qdrant.client.scroll(
            COLLECTION, limit=100, offset=offset, with_payload=True, with_vectors=False
        )
        for record in records:
            key = str(record.id)
            if key not in expected or record.payload != expected[key]:
                raise EvaluationError(
                    "Eval collection contains another corpus/stale payload; use --recreate"
                )
            seen.add(key)
        if offset is None:
            break
    if complete and (seen != set(expected) or await qdrant.count(COLLECTION) != len(expected)):
        raise EvaluationError("Incomplete index: indexed IDs do not equal all frozen chunk IDs")


async def run_evaluation(
    settings: Settings, suite: EvalSuite, snapshot: CorpusSnapshot, *, recreate: bool = False
) -> dict:
    expected_revision = "alibaba_dashscope:qwen3.7-text-embedding:d1024:dense:title-section-text-v1"
    revision = build_embedding_revision(settings)
    if revision != expected_revision or settings.tenant_id != snapshot.tenant_id:
        raise EvaluationError(
            "Evaluation requires the fixed baseline embedding and configured tenant"
        )
    started = datetime.now(UTC)
    async with AsyncExitStack() as resources:
        database = Database(settings)
        resources.push_async_callback(database.close)
        async with database.sessions() as session:
            corpora = await load_frozen_corpus(session, snapshot)
        # Resolve before paid requests and mutation so broken Gold fails without side effects.
        gold = {case.id: resolve_gold(case, corpora) for case in suite.cases}
        print(
            f"Corpus: {snapshot.corpus_id} documents={len(corpora)} chunks={snapshot.chunk_count}",
            flush=True,
        )
        chunks = [
            (c.document, c.version, chunk)
            for c in sorted(corpora, key=lambda c: c.document.external_key)
            for chunk in c.chunks
        ]
        embedding = EmbeddingAdapter(settings)
        resources.push_async_callback(embedding.close)
        vectors = await embed_documents_batched(
            embedding,
            [
                build_embedding_text(doc.title, chunk.section_path, chunk.text)
                for doc, _, chunk in chunks
            ],
        )
        print(f"Embedding: {revision} vectors={len(vectors)} batch_size={BATCH_SIZE}", flush=True)
        # Do not index data whose approval/content changed while waiting for the provider.
        async with database.sessions() as session:
            await load_frozen_corpus(session, snapshot)
        points = [
            models.PointStruct(
                id=str(chunk.id),
                vector=vector,
                payload=build_qdrant_payload(doc, version, chunk, revision),
            )
            for (doc, version, chunk), vector in zip(chunks, vectors, strict=True)
        ]
        qdrant = QdrantConnection(settings)
        resources.push_async_callback(qdrant.close)
        prepare = qdrant.recreate_collection if recreate else qdrant.ensure_collection
        await prepare(
            COLLECTION, dimension=settings.embedding_dimension, embedding_revision=revision
        )
        await validate_index(qdrant, points, complete=False)
        await qdrant.upsert(
            COLLECTION, points, dimension=settings.embedding_dimension, embedding_revision=revision
        )
        await validate_index(qdrant, points, complete=True)
        print(f"Collection: {COLLECTION} Points: {len(points)} recreate={recreate}", flush=True)
        query_filter = payload_filter(
            {
                "tenant_id": snapshot.tenant_id,
                "kb_id": str(snapshot.kb_id),
                "embedding_revision": revision,
                "embedding_input_version": EMBEDDING_INPUT_VERSION,
            }
        )
        query_filter.must.append(models.HasIdCondition(has_id=[point.id for point in points]))
        results = []
        audiences = {doc.external_key: set(doc.audiences) for doc in snapshot.documents}
        for case in suite.cases:
            vector = await embedding.embed_query(case.query)
            hits = await qdrant.search(COLLECTION, vector, query_filter=query_filter, top_k=TOP_K)
            if len(hits) != TOP_K:
                raise EvaluationError(f"{case.id}: expected five hits")
            try:
                ids = [UUID(str(hit.id)) for hit in hits]
            except ValueError:
                raise EvaluationError("Qdrant returned a non-UUID point ID") from None
            async with database.sessions() as session:
                rows = await hydrate_frozen_chunks(session, snapshot, ids)
            evidence = []
            for rank, (hit, (doc, version, chunk)) in enumerate(zip(hits, rows, strict=True), 1):
                if hit.payload != build_qdrant_payload(doc, version, chunk, revision):
                    raise EvaluationError(f"{case.id}: Qdrant payload differs from PostgreSQL")
                entry = {
                    "rank": rank,
                    "score": hit.score,
                    "external_key": doc.external_key,
                    "chunk_id": str(chunk.id),
                    "version_id": str(version.id),
                    "ordinal": chunk.ordinal,
                    "title": doc.title,
                    "section_path": chunk.section_path,
                    "audiences": version.audiences,
                    "text_preview": chunk.text[:320],
                }
                if case.category == "source_conflict":
                    entry["text"] = chunk.text
                evidence.append(entry)
            result = case_result(
                case,
                gold[case.id],
                evidence,
                set().union(*(audiences[ref.external_key] for ref in case.gold_refs)),
            )
            results.append(result)
            print(
                f"{case.id} {case.category}: Gold rank={result['gold_rank']} "
                f"{result.get('observation', '')}",
                flush=True,
            )
        async with database.sessions() as session:
            await load_frozen_corpus(session, snapshot)
        await validate_index(qdrant, points, complete=True)
    return {
        "run_timestamp": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "status": "complete",
        "suite": suite.suite,
        "corpus_id": snapshot.corpus_id,
        "corpus_fingerprint": fingerprint(snapshot),
        "suite_fingerprint": fingerprint(suite),
        "document_count": len(snapshot.documents),
        "chunk_count": snapshot.chunk_count,
        "documents": [
            doc.model_dump(mode="json", exclude={"chunks"}) | {"chunk_count": len(doc.chunks)}
            for doc in snapshot.documents
        ],
        "embedding_revision": revision,
        "embedding_batch_size": BATCH_SIZE,
        "collection": COLLECTION,
        "distance": "COSINE",
        "top_k": TOP_K,
        "audience_filter": False,
        "point_count": len(points),
        "recreate": recreate,
        "query_count": len(results),
        "scored_count": sum(case.scored for case in suite.cases),
        "no_answer_count": sum(case.category == "no_answer" for case in suite.cases),
        "source_conflict_count": sum(case.category == "source_conflict" for case in suite.cases),
        **aggregate_metrics(results),
        "cases": results,
    }


def fingerprint(model: FrozenModel) -> str:
    from rag_portfolio.services.chunking import text_hash

    return text_hash(json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))


def render_report(report: dict) -> str:
    lines = [
        "# Multi-document Dense Evaluation v1",
        "",
        f"Run: {report['run_timestamp']}",
        f"Corpus: {report['corpus_id']}",
        f"Documents: {report['document_count']} / chunks: {report['chunk_count']} "
        f"/ points: {report['point_count']}",
        f"Embedding: {report['embedding_revision']}; batch size: {report['embedding_batch_size']}",
        f"Collection: {report['collection']}; Top-K: {report['top_k']}; audience filter: false",
        f"Queries: {report['query_count']}; scored: {report['scored_count']}; "
        f"no-answer: {report['no_answer_count']}; "
        f"source-conflict: {report['source_conflict_count']}",
        "",
        "## Overall / Per-category",
        "",
        "| Category | Count | Recall@1 | Recall@3 | Recall@5 | MRR@5 | DocHit@1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in {"overall": report["overall"], **report["per_category"]}.items():
        values = " | ".join(
            f"{metrics[key]:.6f}"
            for key in ("Recall@1", "Recall@3", "Recall@5", "MRR@5", "DocHit@1")
        )
        lines.append(f"| {name} | {metrics['count']} | {values} |")
    lines += ["", "## Failures / Near misses", ""]
    for code, label in FAILURE_LABELS.items():
        lines.append(f"- {label}: {', '.join(report['failures'][code]) or 'none'}")
    lines += ["", "## Case results", ""]
    for result in report["cases"]:
        rank = result["gold_rank"]
        rank_label = rank if rank is not None else "not in Top-5 / not applicable"
        lines += [
            f"### {result['id']} — {result['category']}",
            "",
            result["query"],
            "",
            f"Gold chunk IDs: {', '.join(result['gold_chunk_ids']) or 'none'}",
            f"Gold documents: {', '.join(result['gold_documents']) or 'none'}",
            f"Gold rank: {rank_label}",
        ]
        if result.get("excluded_from_quality_metrics"):
            lines += [
                f"**{result['observation']} — excluded_from_quality_metrics**",
                f"Top1 score: {result['top1_score']}; document: {result['top1_document']}; "
                f"section: {result['top1_section']}",
            ]
        lines.append("")
        for hit in result["top5"]:
            lines += [
                f"[{hit['rank']}] score={hit['score']:.6f} external_key={hit['external_key']} "
                f"chunk_id={hit['chunk_id']} ordinal={hit['ordinal']}",
                f"Section: {hit['section_path']}",
                "",
                *(f"> {line}" for line in hit.get("text", hit["text_preview"]).splitlines()),
                "",
            ]
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".eval-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_report(report: dict, output_dir: Path) -> None:
    contents = {
        "json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        "md": render_report(report),
    }
    run_id = datetime.fromisoformat(report["run_timestamp"]).strftime("%Y%m%dT%H%M%S%fZ")
    for suffix, content in contents.items():
        atomic_write(output_dir / "runs" / f"{run_id}.{suffix}", content)
        atomic_write(output_dir / f"latest.{suffix}", content)
