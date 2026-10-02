"""Dense baseline helpers; PostgreSQL remains the source of chunk text and approval state."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_portfolio.config import Settings
from rag_portfolio.db.models import DocumentChunk, DocumentVersion, SourceDocument

EMBEDDING_INPUT_VERSION = "title-section-text-v1"


class DenseRetrievalError(RuntimeError):
    """An unapproved, missing or stale corpus cannot be used for retrieval."""


def build_embedding_text(title: str, section_path: str, text: str) -> str:
    return "\n\n".join(
        value for value in (title.strip(), section_path.strip(), text.strip()) if value
    )


def build_embedding_revision(settings: Settings) -> str:
    return (
        f"{settings.embedding_provider}:{settings.embedding_model}:"
        f"d{settings.embedding_dimension}:{settings.embedding_output_type}:{EMBEDDING_INPUT_VERSION}"
    )


def build_qdrant_payload(
    document: SourceDocument,
    version: DocumentVersion,
    chunk: DocumentChunk,
    embedding_revision: str,
) -> dict[str, str | int]:
    return {
        "tenant_id": chunk.tenant_id,
        "kb_id": str(version.kb_id),
        "document_id": str(document.id),
        "version_id": str(version.id),
        "chunk_id": str(chunk.id),
        "ordinal": chunk.ordinal,
        "section_path": chunk.section_path,
        "chunker_version": chunk.chunker_version,
        "content_hash": chunk.content_hash,
        "embedding_revision": embedding_revision,
        "embedding_input_version": EMBEDDING_INPUT_VERSION,
    }


@dataclass(frozen=True)
class ApprovedCorpus:
    document: SourceDocument
    version: DocumentVersion
    chunks: list[DocumentChunk]


async def load_approved_corpus(
    session: AsyncSession, tenant_id: str, version_id: UUID
) -> ApprovedCorpus:
    row = (
        await session.execute(
            select(SourceDocument, DocumentVersion)
            .join(
                DocumentVersion,
                (DocumentVersion.document_id == SourceDocument.id)
                & (DocumentVersion.kb_id == SourceDocument.kb_id)
                & (DocumentVersion.tenant_id == SourceDocument.tenant_id),
            )
            .where(DocumentVersion.id == version_id, DocumentVersion.tenant_id == tenant_id)
        )
    ).one_or_none()
    if row is None:
        raise DenseRetrievalError("DocumentVersion not found for the configured tenant")
    document, version = row
    if version.review_status != "approved" or version.revoked_at is not None:
        raise DenseRetrievalError("DocumentVersion must be approved and not revoked")
    chunks = list(
        await session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.version_id == version.id, DocumentChunk.tenant_id == tenant_id)
            .order_by(DocumentChunk.ordinal.asc())
        )
    )
    if not chunks or len({chunk.chunker_version for chunk in chunks}) != 1:
        raise DenseRetrievalError("Expected nonempty chunks from exactly one chunker revision")
    return ApprovedCorpus(document, version, chunks)


async def hydrate_chunks(
    session: AsyncSession, corpus: ApprovedCorpus, chunk_ids: list[UUID]
) -> list[tuple[SourceDocument, DocumentChunk]]:
    """Read current rows by Qdrant IDs, recheck approval/scope, preserve retrieval order."""
    if len(set(chunk_ids)) != len(chunk_ids):
        raise DenseRetrievalError("Duplicate Qdrant point IDs")
    rows = (
        await session.execute(
            select(SourceDocument, DocumentChunk)
            .select_from(DocumentChunk)
            .join(
                DocumentVersion,
                (DocumentVersion.id == DocumentChunk.version_id)
                & (DocumentVersion.tenant_id == DocumentChunk.tenant_id),
            )
            .join(
                SourceDocument,
                (SourceDocument.id == DocumentVersion.document_id)
                & (SourceDocument.kb_id == DocumentVersion.kb_id)
                & (SourceDocument.tenant_id == DocumentVersion.tenant_id),
            )
            .where(
                DocumentChunk.id.in_(chunk_ids),
                DocumentChunk.tenant_id == corpus.version.tenant_id,
                DocumentChunk.version_id == corpus.version.id,
                DocumentChunk.chunker_version == corpus.chunks[0].chunker_version,
                DocumentVersion.kb_id == corpus.version.kb_id,
                DocumentVersion.document_id == corpus.document.id,
                DocumentVersion.review_status == "approved",
                DocumentVersion.revoked_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    by_id = {chunk.id: (document, chunk) for document, chunk in rows}
    if set(by_id) != set(chunk_ids):
        raise DenseRetrievalError("Hydration failed: missing, out-of-scope or unapproved chunk")
    return [by_id[chunk_id] for chunk_id in chunk_ids]
