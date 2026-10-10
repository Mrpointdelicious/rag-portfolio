import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from rag_portfolio.evaluation.corpus import EvaluationError
from rag_portfolio.evaluation.retrieval import DEFAULT_SUITE_PATH, read_suite
from rag_portfolio.evaluation.retrievers import (
    ROLE_BOOST,
    ArchivedDenseRetriever,
    Candidate,
    LocalBM25Retriever,
    reciprocal_rank_fusion,
    role_boost,
    tokenize,
)
from rag_portfolio.evaluation.v2_metrics import (
    aggregate,
    answerability_report,
    evaluate_case,
    threshold_metrics,
)
from rag_portfolio.evaluation.v2_runner import validate_output_dir
from rag_portfolio.evaluation.v2_schema import (
    BASELINE_REVISION,
    DEFAULT_V2_SUITE,
    EvalCaseV2,
    read_dense_source,
    read_v2_suite,
    validate_v2_inputs,
)


@pytest.fixture
def suite():
    return read_v2_suite(DEFAULT_V2_SUITE)


def find_case(suite, case_id):
    return next(case for case in suite.cases if case.id == case_id)


def hit(chunk_id, score=0.8, document="hospital-scene-guide"):
    return {"chunk_id": str(chunk_id), "score": score, "external_key": document}


def test_v2_preserves_queries_and_review_rationales(suite):
    old = read_suite(DEFAULT_SUITE_PATH)
    assert len(suite.cases) == 29
    assert [
        (case.id, case.query, case.category, case.scored, case.answerable) for case in suite.cases
    ] == [(case.id, case.query, case.category, case.scored, case.answerable) for case in old.cases]
    assert sum(case.scored for case in suite.cases) == 25
    assert all(case.gold_review_reason and case.required_facts for case in suite.cases)
    assert len(find_case(suite, "P02").acceptable_chunk_ids) == 1
    assert find_case(suite, "R02").query_context.requester_role == "doctor"
    assert find_case(suite, "R02").query_context.target_role == "patient"
    assert find_case(suite, "R03").query_context.requester_role == "patient"
    assert find_case(suite, "R03").query_context.target_role == "doctor"


@pytest.mark.parametrize("problem", ["preferred", "duplicate", "rationale", "unscored"])
def test_schema_rejects_inconsistent_evidence(suite, problem):
    raw = find_case(suite, "E01").model_dump(mode="json")
    if problem == "preferred":
        raw["preferred_chunk_ids"] = [str(uuid4())]
    elif problem == "duplicate":
        raw["acceptable_chunk_ids"].append(raw["acceptable_chunk_ids"][0])
    elif problem == "rationale":
        raw["acceptable_evidence"].clear()
    else:
        raw["scored"] = False
    with pytest.raises(ValidationError):
        EvalCaseV2.model_validate(raw)


@pytest.mark.parametrize("case_id", ["N01", "C01"])
def test_nonquality_cases_cannot_be_scored(suite, case_id):
    raw = find_case(suite, case_id).model_dump(mode="json")
    raw["scored"] = True
    with pytest.raises(ValidationError):
        EvalCaseV2.model_validate(raw)


def test_acceptable_does_not_require_preferred_and_doc_hit_is_separate(suite):
    case = find_case(suite, "E01")
    preferred = case.preferred_chunk_ids[0]
    alternative = next(key for key in case.acceptable_chunk_ids if key != preferred)
    row = evaluate_case(case, [hit(alternative), hit(preferred, 0.7)])
    assert row["metrics"]["Recall@1"] == 1
    assert row["metrics"]["MRR@5"] == 1
    assert row["metrics"]["PreferredHit@1"] == 0
    assert row["metrics"]["PreferredHit@3"] == 1
    row = evaluate_case(case, [hit(uuid4()), hit(alternative, 0.7)])
    assert row["metrics"]["MRR@5"] == 0.5
    assert row["metrics"]["DocHit@1"] == 1
    assert row["metrics"]["Recall@1"] == 0


def test_missing_evidence_and_aggregate_exclude_noanswer_conflict(suite):
    missing = evaluate_case(find_case(suite, "P02"), [hit(uuid4())])
    no_answer = evaluate_case(find_case(suite, "N01"), [hit(uuid4())])
    conflict = evaluate_case(find_case(suite, "C01"), [hit(uuid4())])
    assert "retrieval failure" in missing["taxonomy"]
    assert "retrieval failure" not in conflict["taxonomy"]
    result = aggregate([missing, no_answer, conflict])
    assert result["count"] == 1
    assert result["Recall@5"] == result["MRR@5"] == 0
    assert no_answer["top1_top2_margin"] is None


def test_conflict_requires_both_source_groups(suite):
    case = find_case(suite, "C01")
    left, right = (source.chunk_ids[0] for source in case.conflict_sources)
    row = evaluate_case(case, [hit(left), hit(uuid4(), 0.7), hit(right, 0.6)])
    assert row["conflict_metrics"] == {
        "ConflictRecall@2": 0,
        "ConflictRecall@3": 1,
        "ConflictRecall@5": 1,
    }
    assert row["metrics"] is None
    assert aggregate([row])["count"] == 0


def test_threshold_ties_boundaries_and_zero_accepted():
    row = threshold_metrics([0.8, 0.6], [0.6, 0.3], 0.6)
    assert [row[key] for key in ("TP", "FP", "TN", "FN")] == [2, 1, 1, 0]
    assert row["precision"] == 2 / 3 and row["recall"] == 1
    assert row["false_accept_rate"] == 0.5 and row["false_reject_rate"] == 0
    rejected = threshold_metrics([0.8, 0.6], [0.6, 0.3], 0.9)
    assert rejected["precision"] is None
    assert rejected["recall"] == 0 and rejected["false_reject_rate"] == 1


def test_score_sweep_excludes_conflict_and_detects_overlap(suite):
    rows = [
        evaluate_case(find_case(suite, "E01"), [hit(uuid4(), 0.8), hit(uuid4(), 0.7)]),
        evaluate_case(find_case(suite, "E02"), [hit(uuid4(), 0.5), hit(uuid4(), 0.4)]),
        evaluate_case(find_case(suite, "N01"), [hit(uuid4(), 0.6), hit(uuid4(), 0.4)]),
        evaluate_case(find_case(suite, "C01"), [hit(uuid4(), 0.99), hit(uuid4(), 0.9)]),
    ]
    result = answerability_report(rows, "cosine")
    assert not result["strictly_separable"]
    assert result["positive_distribution"]["count"] == 2
    assert result["negative_distribution"]["count"] == 1
    assert result["negatives"][0]["margin"] == pytest.approx(0.2)
    assert result["max_recall_at_zero_false_accept"]["recall"] == 0.5
    assert result["min_false_accept_at_full_recall"]["false_accept_rate"] == 1
    assert result["threshold_candidates"][-1]["precision"] is None


def test_tokenizer_is_fixed_and_nfkc_casefolded():
    assert tokenize("医院 PDF ＡＢＣ１２ 5层！") == ["医", "院", "医院", "pdf", "abc12", "5", "层"]
    assert tokenize("!!!") == []


async def test_bm25_matches_hand_calculation_and_ties_by_uuid():
    first, second = UUID(int=1), UUID(int=2)
    retriever = LocalBM25Retriever({second: "alpha beta", first: "alpha beta"})
    hits = await retriever.search("ALPHA alpha", limit=5)
    assert [item.chunk_id for item in hits] == [first, second]
    assert hits[0].score == pytest.approx(math.log1p(0.5 / 2.5))
    assert hits[0].sparse_rank == 1
    assert await retriever.search("unknown", limit=5) == []
    assert await LocalBM25Retriever({first: "!"}).search("alpha", limit=5) == []


async def test_bm25_length_normalization_and_repeated_terms():
    first, second, third = UUID(int=1), UUID(int=2), UUID(int=3)
    retriever = LocalBM25Retriever({first: "alpha alpha", second: "alpha beta", third: "beta beta"})
    hits = await retriever.search("alpha", limit=2)
    assert [item.chunk_id for item in hits] == [first, second]
    assert hits[0].score == pytest.approx(math.log1p(1.5 / 2.5) * 2 * 2.2 / 3.2)


def test_rrf_uses_ranks_not_incomparable_scores_and_deterministic_ties():
    a, b, c = UUID(int=1), UUID(int=2), UUID(int=3)
    dense = [Candidate(a, 0.9), Candidate(b, 0.8)]
    sparse = [Candidate(c, 100), Candidate(b, 0.1)]
    fused = reciprocal_rank_fusion(dense, sparse)
    assert [item.chunk_id for item in fused] == [b, a, c]
    assert fused[0].score == pytest.approx(2 / 62)
    assert fused[0].dense_rank == fused[0].sparse_rank == 2
    assert fused[0].dense_score == 0.8 and fused[0].sparse_score == 0.1
    assert [item.fused_rank for item in fused] == [1, 2, 3]
    with pytest.raises(EvaluationError, match="Duplicate"):
        reciprocal_rank_fusion([dense[0], dense[0]], sparse)
    with pytest.raises(EvaluationError, match="positive"):
        reciprocal_rank_fusion(dense, sparse, k=0)


@pytest.mark.parametrize("case_id", ["R02", "R03"])
def test_metadata_uses_target_role_without_filtering_requester_or_general_docs(suite, case_id):
    context = find_case(suite, case_id).query_context
    requester, general_target, hospital_target, neutral = (UUID(int=i) for i in range(1, 5))
    candidates = [
        Candidate(key, 0.7, dense_rank=rank)
        for rank, key in enumerate((requester, general_target, hospital_target, neutral), 1)
    ]
    roles = {
        requester: context.requester_role,
        general_target: context.target_role,
        hospital_target: context.target_role,
        neutral: None,
    }
    ranked = role_boost(candidates, context.target_role, roles)
    assert [item.chunk_id for item in ranked] == [
        general_target,
        hospital_target,
        requester,
        neutral,
    ]
    assert len(ranked) == 4
    assert ranked[0].metadata_boost == ranked[1].metadata_boost == ROLE_BOOST
    assert role_boost(candidates, None, roles) == candidates
    assert all(item.metadata_boost == 0 for item in candidates)


async def test_archived_dense_is_verified_result_replay_not_gold_lookup(suite, tmp_path):
    keys = [uuid4() for _ in range(5)]
    report = {
        "cases": [
            {
                "query": "synthetic query",
                "top5": [
                    {"chunk_id": str(key), "rank": rank, "score": 1 - rank * 0.1}
                    for rank, key in enumerate(keys, 1)
                ],
            }
        ]
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    checked_suite = suite.model_copy(
        update={"dense_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    )
    loaded = read_dense_source(path, checked_suite)
    retriever = ArchivedDenseRetriever(loaded)
    hits = await retriever.search("synthetic query", limit=5)
    assert [hit.chunk_id for hit in hits] == keys
    assert hits[0].dense_score == 0.9
    for query, limit in (("unknown", 5), ("synthetic query", 6)):
        with pytest.raises(EvaluationError, match="Top-5"):
            await retriever.search(query, limit=limit)
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(EvaluationError, match="SHA256"):
        read_dense_source(path, checked_suite)


@pytest.fixture
def provenance(suite, monkeypatch):
    # Synthetic metadata only; no private corpus/report needed by the test suite.
    import rag_portfolio.evaluation.v2_schema as schema

    monkeypatch.setattr(schema, "fingerprint", lambda _: "0" * 64)
    reviewed = suite.model_copy(
        update={"corpus_fingerprint": "0" * 64, "v1_suite_fingerprint": "0" * 64}
    )
    ids = set(
        key
        for case in suite.cases
        for key in (*case.acceptable_chunk_ids, *case.old_gold_chunk_ids)
    )
    docs = {
        key: SimpleNamespace(external_key=key, version_id=uuid4(), chunks=[])
        for key in suite.document_metadata
    }
    mapping = {}
    # Allocate each reviewed evidence ID to a compatible source.
    for case in suite.cases:
        for evidence in case.acceptable_chunk_ids:
            mapping.setdefault(evidence, case.acceptable_document_keys[0])
        for group in case.conflict_sources:
            for evidence in group.chunk_ids:
                mapping[evidence] = group.external_key
    # Multi-document Gold includes IDs shared with narrower cases; those constrain identity.
    for case in suite.cases:
        if len(case.acceptable_document_keys) == 1:
            for evidence in case.acceptable_chunk_ids:
                mapping[evidence] = case.acceptable_document_keys[0]
    # S04/M01 contain doctor navigation duplicates not separately asked elsewhere.
    mapping[UUID("c9bc6209-00dc-5495-b709-d316a829c572")] = "yueyang-doctor-guide"
    mapping[UUID("130f4d00-1032-5988-8044-78738c4b86f7")] = "yueyang-doctor-guide"
    mapping[UUID("11ce42dd-1c7e-546a-9acd-39d6a79bb35a")] = "yueyang-patient-guide"
    for key in sorted(ids, key=str):
        doc = docs[mapping[key]]
        doc.chunks.append(
            SimpleNamespace(chunk_id=key, ordinal=len(doc.chunks), section_path="synthetic")
        )
    snapshot = SimpleNamespace(
        documents=list(docs.values()), version_ids=docs, chunk_count=len(ids)
    )
    lookup = {chunk.chunk_id: (doc, chunk) for doc in snapshot.documents for chunk in doc.chunks}
    hits = [
        {
            "chunk_id": str(key),
            "external_key": doc.external_key,
            "version_id": str(doc.version_id),
            "ordinal": chunk.ordinal,
            "section_path": chunk.section_path,
            "rank": rank,
            "score": 1 - rank * 0.1,
        }
        for rank, (key, (doc, chunk)) in enumerate(list(lookup.items())[:5], 1)
    ]
    source = {
        "status": "complete",
        "suite": "hospital_dense_v1",
        "top_k": 5,
        "embedding_revision": BASELINE_REVISION,
        "corpus_fingerprint": "0" * 64,
        "suite_fingerprint": "0" * 64,
        "run_timestamp": suite.dense_source_run,
        "document_count": 5,
        "chunk_count": len(ids),
        "point_count": len(ids),
        "query_count": 29,
        "scored_count": 25,
        "audience_filter": False,
        "distance": "COSINE",
        "cases": [
            {
                "id": case.id,
                "query": case.query,
                "gold_chunk_ids": [str(key) for key in case.old_gold_chunk_ids],
                "top5": copy.deepcopy(hits),
            }
            for case in suite.cases
        ],
    }
    return reviewed, snapshot, read_suite(DEFAULT_SUITE_PATH), source


def test_provenance_accepts_consistent_synthetic_inputs(provenance):
    validate_v2_inputs(*provenance)


@pytest.mark.parametrize(
    "problem", ["model", "audience", "rank", "duplicate", "score", "version", "query"]
)
def test_provenance_rejects_mixed_or_corrupt_reports(provenance, problem):
    suite, snapshot, v1, source = provenance
    if problem == "model":
        source["embedding_revision"] = "other"
    elif problem == "audience":
        source["audience_filter"] = True
    elif problem == "query":
        source["cases"][0]["query"] = "changed"
    else:
        hit = source["cases"][0]["top5"][0]
        if problem == "duplicate":
            source["cases"][0]["top5"][1] = hit
        else:
            hit[{"rank": "rank", "score": "score", "version": "version_id"}[problem]] = {
                "rank": 3,
                "score": float("nan"),
                "version": str(uuid4()),
            }[problem]
    with pytest.raises(EvaluationError):
        validate_v2_inputs(suite, snapshot, v1, source)


def test_provenance_rejects_fixture_query_change(provenance):
    suite, snapshot, old, source = provenance
    changed = suite.cases[0].model_copy(update={"query": "changed"})
    suite = suite.model_copy(update={"cases": (changed, *suite.cases[1:])})
    with pytest.raises(EvaluationError, match="query/category"):
        validate_v2_inputs(suite, snapshot, old, source)


@pytest.mark.parametrize("problem", ["corpus_hash", "unknown_evidence", "document_identity"])
def test_provenance_rejects_gold_or_corpus_drift(provenance, problem):
    suite, snapshot, old, source = provenance
    if problem == "corpus_hash":
        suite = suite.model_copy(update={"corpus_fingerprint": "1" * 64})
    else:
        case = suite.cases[0]
        updates = (
            {"acceptable_chunk_ids": (uuid4(),)}
            if problem == "unknown_evidence"
            else {"acceptable_document_keys": ("general-patient-guide",)}
        )
        changed = case.model_copy(update=updates)
        suite = suite.model_copy(update={"cases": (changed, *suite.cases[1:])})
    with pytest.raises(EvaluationError):
        validate_v2_inputs(suite, snapshot, old, source)


@pytest.mark.parametrize(
    "path", [".local/eval", ".local/eval/hospital_dense_v1", ".local/eval/hospital_dense_v1/nested"]
)
def test_v2_output_cannot_overwrite_v1(path):
    with pytest.raises(EvaluationError, match="overlap"):
        validate_output_dir(Path(path), [])


def test_v2_output_cannot_contain_input(tmp_path):
    with pytest.raises(EvaluationError, match="input"):
        validate_output_dir(tmp_path, [tmp_path / "suite.json"])
    validate_output_dir(tmp_path / "new", [tmp_path / "suite.json"])
