"""Offline ablation of real v1 Dense results against current, hash-checked approved chunks."""

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from rag_portfolio.evaluation.corpus import (
    CorpusSnapshot,
    EvaluationError,
    hydrate_frozen_chunks,
    load_frozen_corpus,
)
from rag_portfolio.evaluation.retrieval import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SUITE_PATH,
    atomic_write,
    fingerprint,
    read_suite,
    resolve_gold,
)
from rag_portfolio.evaluation.retrievers import (
    BM25_B,
    BM25_K1,
    CANDIDATE_DEPTH,
    ROLE_BOOST,
    RRF_K,
    TOKENIZER_VERSION,
    ArchivedDenseRetriever,
    Candidate,
    DenseRetriever,
    LocalBM25Retriever,
    SparseRetriever,
    reciprocal_rank_fusion,
    role_boost,
)
from rag_portfolio.evaluation.v2_metrics import (
    TAXONOMY,
    aggregate,
    answerability_report,
    evaluate_case,
)
from rag_portfolio.evaluation.v2_schema import BASELINE_REVISION, EvalSuiteV2, validate_v2_inputs
from rag_portfolio.services.dense_retrieval import build_embedding_text

VARIANTS = {
    "dense_v1": ("A", "dense_v1_eval_v2_gold", "cosine"),
    "dense_metadata": ("B", "dense_metadata", "cosine_plus_role_boost"),
    "hybrid_rrf": ("C", "hybrid_rrf", "rrf_rank_score"),
}
SKIP_REASON = (
    "SKIPPED: provider unavailable; no configured reranker adapter/provider. No simulated metrics."
)
CONFIG = {
    "dense_mode": "SHA256-validated real v1 Top-5 replay; no new embedding or index writes",
    "dense_candidate_depth": CANDIDATE_DEPTH,
    "sparse_candidate_depth": CANDIDATE_DEPTH,
    "top_k": 5,
    "rrf_k": RRF_K,
    "bm25_k1": BM25_K1,
    "bm25_b": BM25_B,
    "tokenizer": TOKENIZER_VERSION,
    "sparse_input": "title-section-text-v1",
    "query_term_frequency": "binary",
    "role_boost": ROLE_BOOST,
    "metadata_context": "annotated target_role; requester_role and hospital_id do not rank/filter",
    "audience_filter": False,
    "hybrid_uses_metadata": False,
    "reranker": "provider unavailable",
}


def candidate_record(candidate: Candidate) -> dict:
    return {**asdict(candidate), "chunk_id": str(candidate.chunk_id)}


async def run_v2(
    session: AsyncSession, suite: EvalSuiteV2, snapshot: CorpusSnapshot, source: dict
) -> dict:
    v1_suite = read_suite(DEFAULT_SUITE_PATH)
    validate_v2_inputs(suite, snapshot, v1_suite, source)
    corpora = await load_frozen_corpus(session, snapshot)
    old_gold = {case.id: resolve_gold(case, corpora) for case in v1_suite.cases}
    if any(set(case.old_gold_chunk_ids) != old_gold[case.id] for case in suite.cases):
        raise EvaluationError(
            "Old Gold differs from the original fixture resolved against frozen chunks"
        )
    texts, roles, body_groups = {}, {}, defaultdict(list)
    for corpus in corpora:
        for chunk in corpus.chunks:
            texts[chunk.id] = build_embedding_text(
                corpus.document.title, chunk.section_path, chunk.text
            )
            roles[chunk.id] = suite.document_metadata[corpus.document.external_key].described_role
            body_groups[chunk.content_hash].append(
                {
                    "external_key": corpus.document.external_key,
                    "chunk_id": str(chunk.id),
                    "ordinal": chunk.ordinal,
                    "section_path": chunk.section_path,
                }
            )
    duplicate_groups = [
        group for group in body_groups.values() if len({row["external_key"] for row in group}) > 1
    ]
    duplicate_ids = {row["chunk_id"] for group in duplicate_groups for row in group}
    dense: DenseRetriever = ArchivedDenseRetriever(source)
    sparse: SparseRetriever = LocalBM25Retriever(texts)
    rows_by_variant = {key: [] for key in VARIANTS}
    old_rows = {row["id"]: row for row in source["cases"]}
    for case in suite.cases:
        dense_hits = await dense.search(case.query, limit=CANDIDATE_DEPTH)
        sparse_hits = await sparse.search(case.query, limit=CANDIDATE_DEPTH)
        fused_hits = reciprocal_rank_fusion(dense_hits, sparse_hits)
        rankings = {
            "dense_v1": dense_hits,
            "dense_metadata": role_boost(dense_hits, case.query_context.target_role, roles),
            "hybrid_rrf": fused_hits[:5],
        }
        keys = list(
            dict.fromkeys(item.chunk_id for ranking in rankings.values() for item in ranking)
        )
        hydrated = await hydrate_frozen_chunks(session, snapshot, keys)
        evidence = {chunk.id: (doc, version, chunk) for doc, version, chunk in hydrated}
        for variant, ranking in rankings.items():
            hits = []
            for rank, candidate in enumerate(ranking, 1):
                doc, version, chunk = evidence[candidate.chunk_id]
                hits.append(
                    {
                        **candidate_record(candidate),
                        "rank": rank,
                        "external_key": doc.external_key,
                        "version_id": str(version.id),
                        "ordinal": chunk.ordinal,
                        "section_path": chunk.section_path,
                        "title": doc.title,
                        "text_preview": chunk.text[:400],
                        **({"text": chunk.text} if case.conflict_sources else {}),
                    }
                )
            row = evaluate_case(case, hits)
            if (
                case.scored
                and old_rows[case.id]["top5"][0]["chunk_id"]
                not in {str(key) for key in case.old_gold_chunk_ids}
                and old_rows[case.id]["top5"][0]["chunk_id"]
                in {str(key) for key in case.acceptable_chunk_ids}
            ):
                row["taxonomy"].append("gold-definition issue")
            if any(hit["chunk_id"] in duplicate_ids for hit in hits):
                row["taxonomy"].append("source duplication")
            if variant == "hybrid_rrf":
                row["candidate_pools"] = {
                    "dense": [candidate_record(item) for item in dense_hits],
                    "sparse": [candidate_record(item) for item in sparse_hits],
                    "fused_union": [candidate_record(item) for item in fused_hits],
                }
            rows_by_variant[variant].append(row)
    # A final read catches approvals/content changes made during this evaluation.
    await load_frozen_corpus(session, snapshot)
    timestamp = datetime.now(UTC).isoformat()
    config_hash = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()
    shared = {
        "run_timestamp": timestamp,
        "suite": suite.suite,
        "suite_fingerprint": fingerprint(suite),
        "corpus_fingerprint": fingerprint(snapshot),
        "dense_source_sha256": suite.dense_source_sha256,
        "dense_source_run": suite.dense_source_run,
        "embedding_revision": BASELINE_REVISION,
        "document_count": 5,
        "chunk_count": snapshot.chunk_count,
        "source_point_count": source["point_count"],
        "query_count": len(suite.cases),
        "scored_count": sum(case.scored for case in suite.cases),
        "config": CONFIG,
        "config_fingerprint": config_hash,
        "limitations": [
            (
                "Replay only covers the original 29 queries and Dense "
                "Top-5; no latency or deeper-candidate claim."
            ),
            (
                "Metadata uses reviewed target-role context, not an "
                "evaluated automatic intent classifier."
            ),
            (
                "BM25 and role boost are eval-only. Parameters fixed "
                "before results, not tuned on this suite."
            ),
            (
                "Only 3 no-answer samples; thresholds are diagnostic "
                "in-sample, never a production policy."
            ),
        ],
    }
    reports = {}
    for key, (letter, name, score_type) in VARIANTS.items():
        rows = rows_by_variant[key]
        reports[key] = {
            **shared,
            "status": "complete",
            "variant": letter,
            "name": name,
            "score_type": score_type,
            "overall": aggregate(rows),
            "categories": {
                category: aggregate([row for row in rows if row["category"] == category])
                for category in dict.fromkeys(row["category"] for row in rows if row["scored"])
            },
            "no_answer": answerability_report(rows, score_type),
            "source_conflict": [
                {
                    "id": row["id"],
                    "metrics": row["conflict_metrics"],
                    "groups": row["conflict_group_ranks"],
                }
                for row in rows
                if row["conflict_metrics"] is not None
            ],
            "cases": rows,
        }
    reports["hybrid_reranker"] = {
        **shared,
        "status": "skipped",
        "variant": "D",
        "name": "hybrid_reranker",
        "reason": SKIP_REASON,
        "overall": None,
        "cases": [
            {"id": case.id, "query": case.query, "status": "skipped", "reason": SKIP_REASON}
            for case in suite.cases
        ],
    }
    reports["comparison"] = compare_reports(reports, source, duplicate_groups)
    return reports


def compare_reports(reports: dict, source: dict, duplicate_groups: list) -> dict:
    baseline = reports["dense_v1"]
    variants = {key: {row["id"]: row for row in reports[key]["cases"]} for key in VARIANTS}
    deltas, role_cases = [], []
    for case_id, row in variants["dense_v1"].items():
        states = {
            key: {
                "acceptable_rank": items[case_id]["acceptable_rank"],
                "preferred_rank": items[case_id]["preferred_rank"],
                "top1_document": items[case_id]["top5"][0]["external_key"],
                "top5_ids": [hit["chunk_id"] for hit in items[case_id]["top5"]],
                "metrics": items[case_id]["metrics"],
                "conflict_metrics": items[case_id]["conflict_metrics"],
            }
            for key, items in variants.items()
        }
        changed = any(
            state["top5_ids"] != states["dense_v1"]["top5_ids"] for state in states.values()
        )
        unresolved = row["category"] == "no_answer" or any(
            (
                state["metrics"] is not None
                and (state["acceptable_rank"] != 1 or state["preferred_rank"] != 1)
            )
            or (
                state["conflict_metrics"] is not None
                and not state["conflict_metrics"]["ConflictRecall@2"]
            )
            for state in states.values()
        )
        summary = {
            "id": case_id,
            "category": row["category"],
            "changed": changed,
            "unresolved": unresolved,
            "variants": states,
        }
        if changed or unresolved:
            deltas.append(summary)
        if row["category"] in ("patient_role", "doctor_role", "role_disambiguation"):
            role_cases.append(summary)
    old_rows = {row["id"]: row for row in source["cases"]}
    old_misses = [
        row
        for row in baseline["cases"]
        if row["scored"] and not old_rows[row["id"]]["metrics"]["Recall@1"]
    ]
    return {
        "status": "complete",
        "run_timestamp": baseline["run_timestamp"],
        "config": baseline["config"],
        "config_fingerprint": baseline["config_fingerprint"],
        "suite_fingerprint": baseline["suite_fingerprint"],
        "corpus_fingerprint": baseline["corpus_fingerprint"],
        "dense_source_sha256": baseline["dense_source_sha256"],
        "v1_original_overall": source["overall"],
        "variants": {
            key: {
                "status": report["status"],
                "overall": report["overall"],
                **(
                    {"categories": report["categories"]}
                    if report["status"] == "complete"
                    else {"reason": report["reason"]}
                ),
            }
            for key, report in reports.items()
        },
        "v1_top1_miss_attribution": {
            "original_misses": [row["id"] for row in old_misses],
            "gold_definition": [row["id"] for row in old_misses if row["acceptable_rank"] == 1],
            "remaining_ranking": [
                row["id"] for row in old_misses if row["acceptable_rank"] not in (None, 1)
            ],
            "retrieval_failure_top5": [
                row["id"] for row in old_misses if row["acceptable_rank"] is None
            ],
        },
        "variant_delta": deltas,
        "role_cases": role_cases,
        "taxonomy_definitions": TAXONOMY,
        "taxonomy_cases": {
            key: {
                label: [row["id"] for row in report["cases"] if label in row["taxonomy"]]
                for label in TAXONOMY
            }
            for key, report in reports.items()
            if report["status"] == "complete"
        },
        "source_duplication": {
            "method": (
                "Exact chunk text content_hash across documents; section/title "
                "not part of comparison. Semantic overlap requires "
                "manual review."
            ),
            "groups": duplicate_groups,
        },
        "limitations": baseline["limitations"],
    }


def validate_output_dir(output_dir: Path, inputs: list[Path]) -> None:
    root = output_dir.resolve()
    v1_root = DEFAULT_OUTPUT_DIR.resolve()
    if root == v1_root or root.is_relative_to(v1_root) or v1_root.is_relative_to(root):
        raise EvaluationError("v2 output must not overlap the v1 artifact directory")
    if any(path.resolve().is_relative_to(root) for path in inputs):
        raise EvaluationError("v2 output must not contain an input file")


def write_v2_reports(reports: dict, output_dir: Path) -> Path:
    from rag_portfolio.evaluation.v2_reports import render_comparison, render_variant

    validate_output_dir(output_dir, [])
    timestamp = datetime.fromisoformat(reports["comparison"]["run_timestamp"])
    archive = output_dir / "runs" / timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    archive.mkdir(parents=True, exist_ok=False)
    outputs = {}
    for key, report in reports.items():
        outputs[f"{key}.json"] = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        outputs[f"{key}.md"] = (
            render_comparison(reports) if key == "comparison" else render_variant(report)
        )
    for name, content in outputs.items():
        atomic_write(archive / name, content)
    for name, content in outputs.items():
        atomic_write(output_dir / name, content)
    atomic_write(
        output_dir / "latest_run.json",
        json.dumps(
            {"run_timestamp": reports["comparison"]["run_timestamp"], "archive": str(archive)},
            indent=2,
        )
        + "\n",
    )
    return archive
