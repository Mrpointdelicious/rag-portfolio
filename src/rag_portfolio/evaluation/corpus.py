"""Freeze exact approved versions and fail closed when PostgreSQL drifts."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_portfolio.db.models import DocumentChunk, DocumentVersion, KnowledgeBase, SourceDocument
from rag_portfolio.services.chunking import text_hash
from rag_portfolio.services.dense_retrieval import ApprovedCorpus
from rag_portfolio.services.documents import DocumentServiceError, verify_version_hashes

CORPUS_ID = "yueyang-multidoc-dense-v1"
HOSPITAL_VERSION_ID = UUID("cc725d7d-f8a9-455d-a613-3ccf71705fae")
CHUNKER_VERSION = "simple-v1:markdown:c800:o80"
DOCUMENT_AUDIENCES = {
    "hospital-scene-guide": {"patient", "doctor"},
    "general-patient-guide": {"patient"},
    "general-doctor-guide": {"doctor"},
    "yueyang-patient-guide": {"patient"},
    "yueyang-doctor-guide": {"doctor"},
}
DEFAULT_CORPUS_PATH = Path(".local/eval/hospital_dense_v1/corpus.json")
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonempty = Annotated[str, Field(min_length=1)]


class EvaluationError(RuntimeError):
    """An invalid experiment must fail rather than silently change corpus or Gold."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FrozenChunk(FrozenModel):
    chunk_id: UUID
    ordinal: int = Field(ge=0)
    section_path: str
    content_hash: Hash


class FrozenDocument(FrozenModel):
    external_key: Nonempty
    document_id: UUID
    version_id: UUID
    revision: int = Field(gt=0)
    title: Nonempty
    source_instance_id: Nonempty
    content_hash: Hash
    metadata_hash: Hash
    audiences: tuple[str, ...]
    chunker_version: Nonempty
    chunks: tuple[FrozenChunk, ...] = Field(min_length=1)


class CorpusSnapshot(FrozenModel):
    corpus_id: Literal["yueyang-multidoc-dense-v1"] = CORPUS_ID
    created_at: datetime
    tenant_id: Nonempty
    kb_id: UUID
    scope_key: Nonempty
    documents: tuple[FrozenDocument, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> "CorpusSnapshot":
        keys = [doc.external_key for doc in self.documents]
        if len(keys) != 5 or set(keys) != set(DOCUMENT_AUDIENCES):
            raise ValueError("Expected five unique corpus external_keys")
        for field in ("document_id", "version_id"):
            if len({getattr(doc, field) for doc in self.documents}) != 5:
                raise ValueError(f"Duplicate {field}")
        chunk_ids = [chunk.chunk_id for doc in self.documents for chunk in doc.chunks]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Duplicate chunk IDs")
        for doc in self.documents:
            if set(doc.audiences) != DOCUMENT_AUDIENCES[doc.external_key]:
                raise ValueError("Corpus audience mismatch")
            if doc.chunker_version != CHUNKER_VERSION:
                raise ValueError("Unexpected chunker revision")
            ordinals = [chunk.ordinal for chunk in doc.chunks]
            if ordinals != list(range(len(ordinals))):
                raise ValueError("Chunks must have contiguous ordered ordinals")
            if doc.external_key == "hospital-scene-guide" and (
                doc.version_id != HOSPITAL_VERSION_ID or doc.revision != 2 or len(doc.chunks) != 10
            ):
                raise ValueError("hospital-scene-guide must retain the original revision 2")
        if self.created_at.tzinfo is None:
            raise ValueError("Snapshot timestamp must include a timezone")
        return self

    @property
    def chunk_count(self) -> int:
        return sum(len(doc.chunks) for doc in self.documents)

    @property
    def version_ids(self) -> dict[str, UUID]:
        return {doc.external_key: doc.version_id for doc in self.documents}


def make_snapshot(
    corpora: list[ApprovedCorpus], *, scope_key: str, created_at: datetime | None = None
) -> CorpusSnapshot:
    keys = [corpus.document.external_key for corpus in corpora]
    if len(keys) != 5 or set(keys) != set(DOCUMENT_AUDIENCES):
        raise EvaluationError("Expected five unique corpus external_keys")
    tenants = {c.document.tenant_id for c in corpora} | {c.version.tenant_id for c in corpora}
    bases = {c.document.kb_id for c in corpora} | {c.version.kb_id for c in corpora}
    if len(tenants) != 1 or len(bases) != 1:
        raise EvaluationError("Corpus must use one tenant and one KB")
    documents = []
    for corpus in sorted(corpora, key=lambda item: item.document.external_key):
        doc, version, chunks = corpus.document, corpus.version, corpus.chunks
        if version.review_status != "approved" or version.revoked_at is not None:
            raise EvaluationError(f"{doc.external_key}: approved, not-revoked version required")
        if doc.id != version.document_id or version.metadata_json.get("format") != "markdown":
            raise EvaluationError(f"{doc.external_key}: version identity or format mismatch")
        try:
            verify_version_hashes(version)
        except DocumentServiceError:
            raise EvaluationError(f"{doc.external_key}: database version hash mismatch") from None
        if not chunks or len({chunk.chunker_version for chunk in chunks}) != 1:
            raise EvaluationError(f"{doc.external_key}: nonempty chunks with one chunker required")
        for chunk in chunks:
            if (
                chunk.version_id != version.id
                or chunk.tenant_id != version.tenant_id
                or text_hash(chunk.text) != chunk.content_hash
            ):
                raise EvaluationError(
                    f"{doc.external_key}: chunk identity or content hash mismatch"
                )
        documents.append(
            FrozenDocument(
                external_key=doc.external_key,
                document_id=doc.id,
                version_id=version.id,
                revision=version.revision,
                title=doc.title,
                source_instance_id=doc.source_instance_id,
                content_hash=version.content_hash,
                metadata_hash=version.metadata_hash,
                audiences=tuple(sorted(version.audiences)),
                chunker_version=chunks[0].chunker_version,
                chunks=tuple(
                    FrozenChunk(
                        chunk_id=chunk.id,
                        ordinal=chunk.ordinal,
                        section_path=chunk.section_path,
                        content_hash=chunk.content_hash,
                    )
                    for chunk in sorted(chunks, key=lambda item: item.ordinal)
                ),
            )
        )
    try:
        return CorpusSnapshot(
            created_at=created_at or datetime.now(UTC),
            tenant_id=next(iter(tenants)),
            kb_id=next(iter(bases)),
            scope_key=scope_key,
            documents=tuple(documents),
        )
    except ValidationError:
        raise EvaluationError(
            "Corpus identity, audiences, chunker or hospital revision mismatch"
        ) from None


async def fetch_corpora(
    session: AsyncSession, tenant_id: str, version_ids: dict[str, UUID]
) -> list[ApprovedCorpus]:
    if set(version_ids) != set(DOCUMENT_AUDIENCES) or len(set(version_ids.values())) != 5:
        raise EvaluationError("Select exactly the five distinct corpus versions")
    rows = (
        await session.execute(
            select(SourceDocument, DocumentVersion)
            .join(
                DocumentVersion,
                (DocumentVersion.document_id == SourceDocument.id)
                & (DocumentVersion.tenant_id == SourceDocument.tenant_id)
                & (DocumentVersion.kb_id == SourceDocument.kb_id),
            )
            .where(
                DocumentVersion.id.in_(version_ids.values()), DocumentVersion.tenant_id == tenant_id
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(rows) != 5 or any(version_ids.get(doc.external_key) != ver.id for doc, ver in rows):
        raise EvaluationError("Selected versions are missing or have a different tenant/document")
    chunks = await session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.version_id.in_(version_ids.values()))
        .order_by(DocumentChunk.ordinal)
        .execution_options(populate_existing=True)
    )
    by_version: dict[UUID, list[DocumentChunk]] = {value: [] for value in version_ids.values()}
    for chunk in chunks:
        by_version[chunk.version_id].append(chunk)
    return [ApprovedCorpus(doc, ver, by_version[ver.id]) for doc, ver in rows]


async def snapshot_from_db(
    session: AsyncSession,
    tenant_id: str,
    version_ids: dict[str, UUID],
    *,
    created_at: datetime | None = None,
) -> tuple[CorpusSnapshot, list[ApprovedCorpus]]:
    corpora = await fetch_corpora(session, tenant_id, version_ids)
    kb = await session.get(KnowledgeBase, corpora[0].version.kb_id, populate_existing=True)
    if kb is None or kb.tenant_id != tenant_id:
        raise EvaluationError("Corpus KB or tenant is missing")
    return make_snapshot(corpora, scope_key=kb.scope_key, created_at=created_at), corpora


async def load_frozen_corpus(
    session: AsyncSession, snapshot: CorpusSnapshot
) -> list[ApprovedCorpus]:
    current, corpora = await snapshot_from_db(
        session, snapshot.tenant_id, snapshot.version_ids, created_at=snapshot.created_at
    )
    if current != snapshot:
        raise EvaluationError(
            "DB drift: frozen corpus differs from PostgreSQL; no version substitution"
        )
    return corpora


async def hydrate_frozen_chunks(
    session: AsyncSession, snapshot: CorpusSnapshot, chunk_ids: list[UUID]
) -> list[tuple[SourceDocument, DocumentVersion, DocumentChunk]]:
    # This small corpus is reread in full (two bulk queries plus KB) on each hydrate.
    # It catches drift even in a document absent from the current query's Top-5.
    corpora = await load_frozen_corpus(session, snapshot)
    by_id = {
        chunk.id: (corpus.document, corpus.version, chunk)
        for corpus in corpora
        for chunk in corpus.chunks
    }
    if len(set(chunk_ids)) != len(chunk_ids) or any(key not in by_id for key in chunk_ids):
        raise EvaluationError("Hydration requires unique chunk IDs from the frozen corpus")
    return [by_id[key] for key in chunk_ids]


def read_snapshot(path: Path) -> CorpusSnapshot:
    try:
        return CorpusSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvaluationError("Cannot read a valid frozen corpus snapshot") from None


def versions_from_import_report(path: Path) -> dict[str, UUID]:
    """Pin the exact imported IDs; approval is checked in PostgreSQL, never inferred here."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        rows = report["documents"]
        expected = set(DOCUMENT_AUDIENCES) - {"hospital-scene-guide"}
        if (
            len(rows) != 4
            or {row["external_key"] for row in rows} != expected
            or any(row["status"] != "success" for row in rows)
        ):
            raise ValueError
        versions = {row["external_key"]: UUID(row["version_id"]) for row in rows}
    except (OSError, ValueError, TypeError, KeyError):
        raise EvaluationError(
            "Import report must contain four successful, unique role documents"
        ) from None
    return {"hospital-scene-guide": HOSPITAL_VERSION_ID, **versions}
