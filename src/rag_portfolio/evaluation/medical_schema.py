"""Medical technical Gold is independent from historical metaverse Gold and approval."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag_portfolio.evaluation.medical_corpus import digest, text_digest
from rag_portfolio.literature_backup import LiteratureError

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1)]
DocumentId = Annotated[str, Field(pattern=r"^D\d{3}$")]


class MedicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MedicalFilters(MedicalModel):
    disease: Text | None = None
    population: Text | None = None
    stage: Text | None = None
    literature_type: Text | None = None
    edition: Text | None = None

    def matches(self, metadata: dict) -> bool:
        return all(
            value in metadata.get(key, [])
            if isinstance(metadata.get(key), list)
            else metadata.get(key) == value
            for key, value in self.model_dump(exclude_none=True).items()
        )


class MedicalCase(MedicalModel):
    id: Annotated[str, Field(pattern=r"^Q\d{2}$")]
    query: Text
    category: Literal[
        "单篇定位", "中英跨语言", "多篇证据", "无答案或缺数据", "适用范围", "版本与差异"
    ]
    split: Literal["dev", "test"]
    candidate_sources: Text
    candidate_page_hint: Text
    review_status: Literal["candidate", "text_verified"]
    filters: MedicalFilters = Field(default_factory=MedicalFilters)


class MedicalSuite(MedicalModel):
    schema_version: Literal[1]
    domain: Literal["medical_knowledge"]
    experiment_id: Literal["medical_rehab_v1"]
    cases: tuple[MedicalCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_cases(self) -> "MedicalSuite":
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate medical case ID")
        if {case.split for case in self.cases} != {"dev", "test"}:
            raise ValueError("Both independent development and sealed-test sets are required")
        return self


class SourceSpan(MedicalModel):
    document_id: DocumentId
    pdf_page: int = Field(gt=0)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text_sha256: Hash

    @model_validator(mode="after")
    def ordered(self) -> "SourceSpan":
        if self.end_char <= self.start_char:
            raise ValueError("Invalid original-page text range")
        return self


class RequiredEvidenceGroup(MedicalModel):
    id: Text
    support: Text
    alternatives: tuple[SourceSpan, ...] = Field(min_length=1)


class MedicalGoldCase(MedicalModel):
    id: Text
    answerable: bool
    scored: bool
    expected_behavior: Literal[
        "answer_from_evidence", "insufficient_input", "scope_limited", "provenance_only"
    ]
    required_groups: tuple[RequiredEvidenceGroup, ...]
    review_reason: Text
    reviewer: Text
    review_scope: Literal["source_text_and_provenance"]

    @model_validator(mode="after")
    def evidence_rules(self) -> "MedicalGoldCase":
        if len({group.id for group in self.required_groups}) != len(self.required_groups):
            raise ValueError("Duplicate required evidence group")
        if self.expected_behavior == "insufficient_input":
            if self.answerable or self.scored or self.required_groups:
                raise ValueError("Unavailable inputs cannot have fabricated Gold evidence")
        elif not self.answerable or not self.required_groups:
            raise ValueError("An evidence-backed explanation requires reviewed source groups")
        if self.expected_behavior == "provenance_only" and self.scored:
            raise ValueError("File-identity policy is not a scored document-text answer")
        return self


class MedicalGold(MedicalModel):
    schema_version: Literal[1]
    domain: Literal["medical_knowledge"]
    experiment_id: Literal["medical_rehab_v1"]
    corpus_fingerprint: Hash
    suite_fingerprint: Hash
    status: Literal["frozen_technical_review"]
    clinical_approval: Literal[False]
    cases: tuple[MedicalGoldCase, ...]
    test_seal: Hash


def read_suite(path: Path) -> MedicalSuite:
    try:
        return MedicalSuite.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise LiteratureError("Invalid or cross-domain medical suite") from exc


def test_seal(suite: MedicalSuite, gold_cases: list[dict], corpus_fingerprint: str) -> str:
    ids = {case.id for case in suite.cases if case.split == "test"}
    return digest(
        {
            "corpus_fingerprint": corpus_fingerprint,
            "cases": [case.model_dump(mode="json") for case in suite.cases if case.id in ids],
            "gold": [row for row in gold_cases if row["id"] in ids],
        }
    )


def validate_gold(
    gold: MedicalGold, suite: MedicalSuite, manifest: dict, pages: list[dict], chunks: list[dict]
) -> dict[str, list[set[str]]]:
    if gold.corpus_fingerprint != manifest[
        "corpus_fingerprint"
    ] or gold.suite_fingerprint != digest(suite.model_dump(mode="json")):
        raise LiteratureError("Medical Gold, suite and corpus fingerprints differ")
    if any(case.review_status != "text_verified" for case in suite.cases):
        raise LiteratureError("Candidate questions must be reviewed before evaluation")
    if {case.id for case in suite.cases} != {case.id for case in gold.cases} or len(
        gold.cases
    ) != len(suite.cases):
        raise LiteratureError("Gold must cover exactly the independent medical suite")
    serialized = [case.model_dump(mode="json") for case in gold.cases]
    if gold.test_seal != test_seal(suite, serialized, gold.corpus_fingerprint):
        raise LiteratureError("Sealed medical test set changed")
    page_map = {(row["document_id"], row["pdf_page"]): row for row in pages}
    bindings = {}
    for case in gold.cases:
        groups = []
        for group in case.required_groups:
            acceptable = set()
            for ref in group.alternatives:
                page = page_map.get((ref.document_id, ref.pdf_page))
                if (
                    page is None
                    or ref.end_char > len(page["text"])
                    or text_digest(page["text"][ref.start_char : ref.end_char]) != ref.text_sha256
                ):
                    raise LiteratureError(f"Original-text Gold span changed: {case.id}/{group.id}")
                if page["quality_flags"]:
                    raise LiteratureError(f"Gold uses an unresolved page: {case.id}/{group.id}")
                acceptable.update(
                    chunk["chunk_id"]
                    for chunk in chunks
                    if chunk["document_id"] == ref.document_id
                    and chunk["pdf_page_start"] == ref.pdf_page
                    and chunk["start_char"] <= ref.start_char
                    and chunk["end_char"] >= ref.end_char
                )
            if not acceptable:
                raise LiteratureError(f"No chunk fully supports Gold span: {case.id}/{group.id}")
            groups.append(acceptable)
        bindings[case.id] = groups
    return bindings


def case_metrics(ranking: list[str], groups: list[set[str]]) -> dict:
    relevant = set().union(*groups) if groups else set()
    first = next((index for index, key in enumerate(ranking[:5], 1) if key in relevant), None)
    covered = sum(bool(set(ranking[:5]) & group) for group in groups)
    return {
        "hit_at_1": float(bool(ranking) and ranking[0] in relevant),
        "hit_at_5": float(first is not None),
        "mrr_at_5": 1 / first if first else 0.0,
        "required_evidence_coverage": covered / len(groups) if groups else None,
        "complete_evidence_at_5": float(covered == len(groups)) if groups else None,
    }
