"""Read-only, pinned technical demo. Never consumes medical snapshots or evaluation reports."""

import hashlib
import hmac
import json
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ConfigDict, Field, model_validator

from rag_portfolio.contracts import Contract
from rag_portfolio.evaluation.retrievers import LocalBM25Retriever


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class DemoModel(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, allow_inf_nan=False)


class DemoQuery(DemoModel):
    query: str = Field(min_length=1, max_length=4000)
    domain: Literal["health", "product", "help"] = "health"
    scope_key: str = Field(min_length=1, max_length=100)
    corpus_version: str = Field(min_length=1, max_length=100)
    top_k: int = Field(default=6, ge=1, le=6, strict=True)
    max_chars: int = Field(default=6000, ge=600, le=6000, strict=True)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.query.strip():
            raise ValueError("Blank query")
        return self


class DemoSection(DemoModel):
    section_id: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    title: str = Field(min_length=1, max_length=200)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class DemoDocument(DemoModel):
    code: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    version: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    title: str = Field(min_length=1, max_length=200)
    domain: Literal["health", "product", "help"]
    source: str = Field(pattern=r"^repo://demo/[a-z0-9-]+$")
    license: Literal["CC0-1.0"]
    approved: bool
    approved_by: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=10000)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sections: list[DemoSection] = Field(min_length=1, max_length=12)


class DemoCorpus(DemoModel):
    schema_version: Literal["demo-1"]
    corpus_version: str
    tenant_id: str
    scope_key: str
    approval: Literal["technical_demo_only"]
    clinical_approval: Literal[False]
    documents: list[DemoDocument] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def verify_sources(self):
        codes = set()
        for doc in self.documents:
            if doc.code in codes or doc.source != f"repo://demo/{doc.code}":
                raise ValueError("Duplicate or inconsistent source")
            codes.add(doc.code)
            if not doc.approved or not doc.approved_by.strip():
                raise ValueError("Unapproved demo document")
            if digest(doc.text) != doc.text_sha256:
                raise ValueError("Document text hash mismatch")
            sections = set()
            for section in doc.sections:
                if (
                    section.section_id in sections
                    or not 0 <= section.start < section.end <= len(doc.text)
                    or section.end - section.start > 1600
                ):
                    raise ValueError("Invalid source span")
                sections.add(section.section_id)
        return self


class DemoEvidence(DemoModel):
    source_id: UUID
    chunk_id: UUID
    doc_ref: str = Field(max_length=200)
    document_version: str = Field(max_length=80)
    title: str = Field(max_length=200)
    source: str = Field(max_length=200)
    section: str = Field(max_length=200)
    section_id: str = Field(max_length=80)
    page: None = None  # These are text documents, so PDF pages are unknown.
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=1600)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score: float = Field(gt=0)


class DemoResponse(DemoModel):
    schema_version: Literal["demo-1"] = "demo-1"
    request_id: UUID
    retrieval_run_id: UUID
    backend: Literal["portfolio_demo_bm25"] = "portfolio_demo_bm25"
    strategy: Literal["bm25-k1-1.2-b-0.75"] = "bm25-k1-1.2-b-0.75"
    approval: Literal["technical_demo_only"] = "technical_demo_only"
    clinical_approval: Literal[False] = False
    corpus_version: str
    corpus_sha256: str
    status: Literal["ok", "no_evidence"]
    elapsed_ms: float = Field(ge=0)
    budget_truncated: bool
    evidence: list[DemoEvidence] = Field(max_length=6)
    signature: str = ""


class DemoIndex:
    def __init__(self, settings):
        path = Path(settings.demo_corpus_path)
        if ".local" in path.parts or not path.is_file() or path.stat().st_size > 500_000:
            raise ValueError("Missing or invalid demo corpus")
        raw = path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(self.sha256, settings.demo_corpus_sha256):
            raise ValueError("Demo corpus pin mismatch")
        self.corpus = DemoCorpus.model_validate_json(raw)
        if (self.corpus.tenant_id, self.corpus.scope_key) != (
            settings.demo_tenant_id,
            settings.demo_scope_key,
        ):
            raise ValueError("Corpus scope mismatch")
        self.rows = {}
        for doc in self.corpus.documents:
            for section in doc.sections:
                cid = uuid5(NAMESPACE_URL, f"{doc.source}@{doc.version}#{section.section_id}")
                self.rows[cid] = (doc, section)
        self.retrievers = {
            domain: LocalBM25Retriever(texts)
            for domain in ("health", "product", "help")
            if (
                texts := {
                    cid: doc.text[s.start : s.end]
                    for cid, (doc, s) in self.rows.items()
                    if doc.domain == domain
                }
            )
        }

    async def search(self, query: DemoQuery) -> tuple[list[DemoEvidence], bool]:
        retriever = self.retrievers.get(query.domain)
        if retriever is None:
            return [], False
        hits = await retriever.search(query.query, limit=query.top_k)
        evidence, used, omitted = [], 0, False
        seen = set()
        for hit in hits:
            doc, section = self.rows[hit.chunk_id]
            text = doc.text[section.start : section.end]
            if (doc.code, digest(text)) in seen:
                continue
            seen.add((doc.code, digest(text)))
            if used + len(text) > query.max_chars:
                omitted = True
                continue  # Atomic excerpts preserve source spans.
            used += len(text)
            evidence.append(
                DemoEvidence(
                    source_id=uuid5(NAMESPACE_URL, doc.source),
                    chunk_id=hit.chunk_id,
                    doc_ref=f"{doc.code}@{doc.version}#{section.section_id}",
                    document_version=doc.version,
                    title=doc.title,
                    source=doc.source,
                    section=section.title,
                    section_id=section.section_id,
                    start=section.start,
                    end=section.end,
                    text=text,
                    text_sha256=digest(text),
                    score=hit.score,
                )
            )
        return evidence, omitted
