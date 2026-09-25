"""Human review and approved-version chunk persistence in a caller-owned transaction."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_portfolio.db.models import DocumentChunk, DocumentVersion
from rag_portfolio.services.chunking import DEFAULT_CHUNKING_CONFIG, ChunkingConfig, chunk_text
from rag_portfolio.services.documents import (
    ActorContext,
    DocumentService,
    DocumentServiceError,
    record_audit,
    require_row_version,
    verify_version_hashes,
)


@dataclass(frozen=True)
class ApprovalResult:
    version: DocumentVersion
    chunks: tuple[DocumentChunk, ...]


class ReviewService:
    def __init__(self, session: AsyncSession, actor: ActorContext):
        self.session = session
        self.actor = actor
        self.documents = DocumentService(session, actor)

    async def _pending_version(
        self,
        version_id: UUID,
        expected_row_version: int,
        expected_content_hash: str,
        expected_metadata_hash: str,
    ) -> DocumentVersion:
        version = await self.documents.lock_version(version_id)
        require_row_version(version, expected_row_version)
        if version.review_status != "pending":
            raise DocumentServiceError("version_not_pending")
        if (
            version.content_hash != expected_content_hash
            or version.metadata_hash != expected_metadata_hash
        ):
            raise DocumentServiceError("review_hash_conflict")
        verify_version_hashes(version)
        return version

    async def approve_version(
        self,
        version_id: UUID,
        *,
        expected_row_version: int,
        expected_content_hash: str,
        expected_metadata_hash: str,
        reason: str,
        chunking: ChunkingConfig = DEFAULT_CHUNKING_CONFIG,
    ) -> ApprovalResult:
        """Approve the reviewed hashes and materialize chunks atomically on commit.

        ``actor`` must identify an authorized reviewer. This service does not infer
        a human identity from an untrusted request field or from the document text.
        """
        if not reason.strip():
            raise DocumentServiceError("review_reason_required", 422)
        version = await self._pending_version(
            version_id, expected_row_version, expected_content_hash, expected_metadata_hash
        )
        if not version.audiences:
            raise DocumentServiceError("audience_required", 422)
        if version.expires_at is not None and version.expires_at <= datetime.now(UTC):
            raise DocumentServiceError("version_expired")
        version.review_status = "approved"
        version.reviewed_by = self.actor.actor_id
        version.reviewed_at = datetime.now(UTC)
        version.row_version += 1
        record_audit(
            self.session,
            self.actor,
            event_type="version.approved",
            entity_type="document_version",
            entity_id=version.id,
            reason=reason.strip(),
            old_ref="pending",
            new_ref=f"approved:{version.content_hash}:{version.metadata_hash}",
        )
        chunks = await self._persist_chunks(version, chunking)
        await self.session.flush()
        return ApprovalResult(version, tuple(chunks))

    async def reject_version(
        self,
        version_id: UUID,
        *,
        expected_row_version: int,
        expected_content_hash: str,
        expected_metadata_hash: str,
        reason: str,
    ) -> DocumentVersion:
        if not reason.strip():
            raise DocumentServiceError("review_reason_required", 422)
        version = await self._pending_version(
            version_id, expected_row_version, expected_content_hash, expected_metadata_hash
        )
        version.review_status = "rejected"
        version.reviewed_by = self.actor.actor_id
        version.reviewed_at = datetime.now(UTC)
        version.row_version += 1
        record_audit(
            self.session,
            self.actor,
            event_type="version.rejected",
            entity_type="document_version",
            entity_id=version.id,
            reason=reason.strip(),
            old_ref="pending",
            new_ref=f"rejected:{version.content_hash}:{version.metadata_hash}",
        )
        await self.session.flush()
        return version

    async def chunk_approved_version(
        self, version_id: UUID, *, config: ChunkingConfig = DEFAULT_CHUNKING_CONFIG
    ) -> list[DocumentChunk]:
        """Idempotently generate chunks for an approved version/configuration."""
        version = await self.documents.lock_version(version_id)
        return await self._persist_chunks(version, config)

    async def _persist_chunks(
        self, version: DocumentVersion, config: ChunkingConfig
    ) -> list[DocumentChunk]:
        if version.review_status != "approved" or version.revoked_at is not None:
            raise DocumentServiceError("approved_version_required")
        if version.expires_at is not None and version.expires_at <= datetime.now(UTC):
            raise DocumentServiceError("version_expired")
        verify_version_hashes(version)
        document_format = version.metadata_json.get("format")
        if document_format not in {"markdown", "text"}:
            raise DocumentServiceError("unsupported_document_format", 422)
        try:
            pieces = chunk_text(version.normalized_text, document_format, config)
        except ValueError as exc:
            raise DocumentServiceError("chunking_failed", 422) from exc
        if not pieces:
            raise DocumentServiceError("no_document_chunks", 422)
        chunker_version = config.version(document_format)
        expected = [
            DocumentChunk(
                id=uuid5(version.id, f"{chunker_version}:{piece.ordinal}:{piece.content_hash}"),
                tenant_id=self.actor.tenant_id,
                version_id=version.id,
                chunker_version=chunker_version,
                ordinal=piece.ordinal,
                section_path=piece.section_path,
                text=piece.text,
                content_hash=piece.content_hash,
                token_count=piece.token_count,
            )
            for piece in pieces
        ]
        existing = list(
            await self.session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.version_id == version.id,
                    DocumentChunk.tenant_id == self.actor.tenant_id,
                    DocumentChunk.chunker_version == chunker_version,
                )
                .order_by(DocumentChunk.ordinal)
                .execution_options(populate_existing=True)
            )
        )
        if existing:
            fields = (
                "id",
                "ordinal",
                "text",
                "section_path",
                "content_hash",
                "token_count",
                "parent_chunk_id",
                "scene_node_id",
                "scene_snapshot_id",
            )
            if len(existing) != len(expected) or any(
                any(getattr(old, key) != getattr(new, key) for key in fields)
                for old, new in zip(existing, expected, strict=True)
            ):
                raise DocumentServiceError("chunk_set_conflict")
            return existing
        self.session.add_all(expected)
        record_audit(
            self.session,
            self.actor,
            event_type="version.chunked",
            entity_type="document_version",
            entity_id=version.id,
            reason="approved_content_chunked",
            new_ref=f"{chunker_version}:{len(expected)}",
        )
        await self.session.flush()
        return expected
