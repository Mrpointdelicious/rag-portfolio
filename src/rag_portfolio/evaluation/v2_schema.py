"""Reviewed acceptable/preferred evidence, bound to the unchanged v1 corpus and queries."""

import hashlib
import json
import math
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from rag_portfolio.evaluation.corpus import (
    CorpusSnapshot,
    EvaluationError,
    FrozenModel,
    Hash,
    Nonempty,
)
from rag_portfolio.evaluation.retrieval import EvalSuite, fingerprint

Role = Literal["patient", "doctor"]
BASELINE_REVISION = "alibaba_dashscope:qwen3.7-text-embedding:d1024:dense:title-section-text-v1"
DEFAULT_V2_SUITE = Path("eval/yueyang_multidoc_v2.json")
DEFAULT_DENSE_SOURCE = Path(".local/eval/hospital_dense_v1/runs/20261004T122146548950Z.json")
DEFAULT_V2_OUTPUT = Path(".local/eval/multidoc_eval_v2")


class QueryContext(FrozenModel):
    requester_role: Role | None
    target_role: Role | None
    hospital_id: Nonempty | None


class DocumentMetadata(FrozenModel):
    described_role: Role | None
    reason: Nonempty


class EvidenceSupport(FrozenModel):
    chunk_id: UUID
    support: Nonempty


class ConflictSource(FrozenModel):
    external_key: Nonempty
    chunk_ids: tuple[UUID, ...] = Field(min_length=1)
    claim: Nonempty


class EvalCaseV2(FrozenModel):
    id: Nonempty
    query: Nonempty
    category: Nonempty
    answerable: StrictBool
    scored: StrictBool
    required_facts: tuple[Nonempty, ...] = Field(min_length=1)
    acceptable_chunk_ids: tuple[UUID, ...]
    preferred_chunk_ids: tuple[UUID, ...]
    acceptable_document_keys: tuple[Nonempty, ...]
    query_context: QueryContext
    old_gold_chunk_ids: tuple[UUID, ...]
    gold_review_reason: Nonempty
    acceptable_evidence: tuple[EvidenceSupport, ...]
    conflict_sources: tuple[ConflictSource, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> "EvalCaseV2":
        acceptable, preferred = set(self.acceptable_chunk_ids), set(self.preferred_chunk_ids)
        if len(acceptable) != len(self.acceptable_chunk_ids) or len(preferred) != len(
            self.preferred_chunk_ids
        ):
            raise ValueError("Duplicate evidence IDs")
        if not preferred <= acceptable:
            raise ValueError("Preferred evidence must be a subset of acceptable evidence")
        if (
            len(self.acceptable_evidence) != len(acceptable)
            or {item.chunk_id for item in self.acceptable_evidence} != acceptable
        ):
            raise ValueError("Every acceptable chunk requires an explicit support rationale")
        if self.category == "no_answer":
            if self.answerable or self.scored or acceptable or preferred or self.conflict_sources:
                raise ValueError("No-answer cannot contain answer evidence or quality metrics")
        elif self.category == "source_conflict":
            if not self.answerable or self.scored or acceptable or preferred:
                raise ValueError("Conflict uses source groups, not a single correct answer")
            if (
                len(self.conflict_sources) != 2
                or len({s.external_key for s in self.conflict_sources}) != 2
            ):
                raise ValueError("Conflict requires two distinct source groups")
            if set(self.conflict_sources[0].chunk_ids) & set(self.conflict_sources[1].chunk_ids):
                raise ValueError("Conflict source groups must not overlap")
        elif (
            not self.answerable
            or not self.scored
            or not acceptable
            or not preferred
            or self.conflict_sources
        ):
            raise ValueError("Quality case requires acceptable and preferred evidence")
        return self


class EvalSuiteV2(FrozenModel):
    suite: Literal["yueyang_multidoc_v2"]
    corpus_fingerprint: Hash
    v1_suite_fingerprint: Hash
    dense_source_sha256: Hash
    dense_source_run: Nonempty
    document_metadata: dict[str, DocumentMetadata]
    cases: tuple[EvalCaseV2, ...] = Field(min_length=29, max_length=29)


def read_v2_suite(path: Path) -> EvalSuiteV2:
    try:
        return EvalSuiteV2.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvaluationError("Invalid Eval v2 fixture") from None


def read_dense_source(path: Path, suite: EvalSuiteV2) -> dict:
    try:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != suite.dense_source_sha256:
            raise EvaluationError("Dense source SHA256 differs from the reviewed v1 run")
        return json.loads(content)
    except (OSError, ValueError):
        raise EvaluationError("Cannot load the frozen Dense v1 report") from None


def validate_v2_inputs(
    suite: EvalSuiteV2, snapshot: CorpusSnapshot, v1_suite: EvalSuite, source: dict
) -> None:
    if (
        fingerprint(snapshot) != suite.corpus_fingerprint
        or fingerprint(v1_suite) != suite.v1_suite_fingerprint
    ):
        raise EvaluationError("Frozen corpus or original v1 query suite changed")
    if set(suite.document_metadata) != set(snapshot.version_ids):
        raise EvaluationError(
            "Described-role metadata must cover exactly the five frozen documents"
        )
    old = {case.id: case for case in v1_suite.cases}
    if len({case.id for case in suite.cases}) != 29 or {case.id for case in suite.cases} != set(
        old
    ):
        raise EvaluationError("The 29 original case IDs must be preserved")
    lookup = {chunk.chunk_id: (doc, chunk) for doc in snapshot.documents for chunk in doc.chunks}
    for case in suite.cases:
        prior = old[case.id]
        if any(
            getattr(case, name) != getattr(prior, name)
            for name in ("query", "category", "answerable", "scored")
        ):
            raise EvaluationError(f"{case.id}: original query/category/scoring flags changed")
        ids = set(case.acceptable_chunk_ids) | set(case.old_gold_chunk_ids)
        ids |= {key for group in case.conflict_sources for key in group.chunk_ids}
        if not ids <= lookup.keys():
            raise EvaluationError(f"{case.id}: evidence does not belong to the frozen corpus")
        if set(case.acceptable_document_keys) != {
            lookup[key][0].external_key for key in case.acceptable_chunk_ids
        }:
            raise EvaluationError(f"{case.id}: acceptable document keys do not match evidence")
        if any(
            lookup[key][0].external_key != group.external_key
            for group in case.conflict_sources
            for key in group.chunk_ids
        ):
            raise EvaluationError(f"{case.id}: conflict source identity mismatch")
    try:
        expected = {
            "status": "complete",
            "suite": "hospital_dense_v1",
            "top_k": 5,
            "embedding_revision": BASELINE_REVISION,
            "corpus_fingerprint": suite.corpus_fingerprint,
            "suite_fingerprint": suite.v1_suite_fingerprint,
            "run_timestamp": suite.dense_source_run,
            "document_count": 5,
            "chunk_count": snapshot.chunk_count,
            "point_count": snapshot.chunk_count,
            "query_count": 29,
            "scored_count": 25,
            "audience_filter": False,
            "distance": "COSINE",
        }
        if any(source[key] != value for key, value in expected.items()):
            raise ValueError
        records = source["cases"]
        if len(records) != 29 or {row["id"] for row in records} != set(old):
            raise ValueError
        by_id = {case.id: case for case in suite.cases}
        for row in records:
            case = by_id[row["id"]]
            if row["query"] != case.query or set(row["gold_chunk_ids"]) != {
                str(key) for key in case.old_gold_chunk_ids
            }:
                raise ValueError
            hits = row["top5"]
            if len(hits) != 5 or len({hit["chunk_id"] for hit in hits}) != 5:
                raise ValueError
            for rank, hit in enumerate(hits, 1):
                doc, chunk = lookup[UUID(hit["chunk_id"])]
                if (
                    hit["rank"] != rank
                    or hit["external_key"] != doc.external_key
                    or hit["version_id"] != str(doc.version_id)
                    or hit["ordinal"] != chunk.ordinal
                    or hit["section_path"] != chunk.section_path
                    or not math.isfinite(hit["score"])
                    or not -1.000001 <= hit["score"] <= 1.000001
                ):
                    raise ValueError
    except (KeyError, TypeError, ValueError):
        raise EvaluationError(
            "Frozen Dense report provenance, ranking or corpus identity mismatch"
        ) from None
