"""Document identities, editable drafts and frozen review submissions.

Call inside ``async with database.sessions.begin() as session``. Services flush
but never commit; let exceptions escape that context so writes and audits roll
back together. The caller must authenticate/authorize the supplied actor.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_portfolio.db.models import AuditEvent, DocumentVersion, KnowledgeBase, SourceDocument
from rag_portfolio.services.chunking import parse_document, text_hash

ALLOWED_AUDIENCES = {"patient", "doctor", "internal"}


class DocumentServiceError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class ActorContext:
    tenant_id: str
    actor_id: str
    request_id: UUID

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.actor_id.strip():
            raise ValueError("tenant_id and actor_id are required")


@dataclass(frozen=True)
class VersionInput:
    filename: str
    content: str | bytes
    source_locator: str
    effective_at: datetime
    audiences: Sequence[str] = ()
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DocumentServiceError("timezone_required", 422)
    return value.astimezone(UTC)


def metadata_hash(
    metadata: dict,
    audiences: Sequence[str],
    effective_at: datetime,
    expires_at: datetime | None,
) -> str:
    payload = {
        "metadata": metadata,
        "audiences": sorted(set(audiences)),
        "effective_at": _utc(effective_at).isoformat(),
        "expires_at": _utc(expires_at).isoformat() if expires_at else None,
    }
    try:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return text_hash(serialized)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DocumentServiceError("invalid_metadata", 422) from exc


def verify_version_hashes(version: DocumentVersion) -> None:
    if (
        text_hash(version.normalized_text) != version.content_hash
        or metadata_hash(
            version.metadata_json, version.audiences, version.effective_at, version.expires_at
        )
        != version.metadata_hash
    ):
        raise DocumentServiceError("version_hash_mismatch")


def require_row_version(version: DocumentVersion, expected: int) -> None:
    if expected != version.row_version:
        raise DocumentServiceError("row_version_conflict")


def record_audit(
    session: AsyncSession,
    actor: ActorContext,
    *,
    event_type: str,
    entity_type: str,
    entity_id: UUID,
    reason: str,
    old_ref: str | None = None,
    new_ref: str | None = None,
) -> None:
    session.add(
        AuditEvent(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            request_id=actor.request_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            old_ref=old_ref,
            new_ref=new_ref,
        )
    )


class DocumentService:
    def __init__(self, session: AsyncSession, actor: ActorContext):
        self.session = session
        self.actor = actor

    async def lock_document(self, document_id: UUID) -> SourceDocument:
        document = await self.session.scalar(
            select(SourceDocument)
            .where(
                SourceDocument.id == document_id,
                SourceDocument.tenant_id == self.actor.tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if document is None:
            raise DocumentServiceError("document_not_found", 404)
        if document.deleted_at is not None or document.lifecycle != "active":
            raise DocumentServiceError("document_inactive")
        return document

    async def lock_version(self, version_id: UUID) -> DocumentVersion:
        """Always acquire document before version locks, including during review."""
        document_id = await self.session.scalar(
            select(DocumentVersion.document_id).where(
                DocumentVersion.id == version_id,
                DocumentVersion.tenant_id == self.actor.tenant_id,
            )
        )
        if document_id is None:
            raise DocumentServiceError("version_not_found", 404)
        await self.lock_document(document_id)
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.tenant_id == self.actor.tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if version is None:
            raise DocumentServiceError("version_not_found", 404)
        if version.revoked_at is not None:
            raise DocumentServiceError("version_revoked")
        return version

    async def create_document(
        self,
        kb_id: UUID,
        *,
        title: str,
        source_instance_id: str,
        external_key: str,
        source_system: str = "upload",
    ) -> tuple[SourceDocument, bool]:
        """Register one source identity in a KB; return (document, created)."""
        values = {
            "title": title.strip(),
            "source_instance_id": source_instance_id.strip(),
            "external_key": external_key.strip(),
            "source_system": source_system.strip(),
        }
        if not all(values.values()):
            raise DocumentServiceError("document_identity_required", 422)
        # Serialize registration in this KB so concurrent identical sources reuse a row.
        kb = await self.session.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == self.actor.tenant_id)
            .with_for_update()
        )
        if kb is None:
            raise DocumentServiceError("knowledge_base_not_found", 404)
        existing = await self.session.scalar(
            select(SourceDocument)
            .where(
                SourceDocument.kb_id == kb_id,
                SourceDocument.tenant_id == self.actor.tenant_id,
                SourceDocument.source_instance_id == values["source_instance_id"],
                SourceDocument.external_key == values["external_key"],
            )
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items()):
                raise DocumentServiceError("document_identity_conflict")
            if existing.deleted_at is not None or existing.lifecycle != "active":
                raise DocumentServiceError("document_inactive")
            return existing, False
        document = SourceDocument(tenant_id=self.actor.tenant_id, kb_id=kb_id, **values)
        self.session.add(document)
        await self.session.flush()
        record_audit(
            self.session,
            self.actor,
            event_type="document.created",
            entity_type="source_document",
            entity_id=document.id,
            reason="source_registered",
            new_ref=values["external_key"],
        )
        await self.session.flush()
        return document, True

    def _prepare(self, document: SourceDocument, body: VersionInput) -> dict:
        try:
            parsed = parse_document(body.filename, body.content)
        except (ValueError, UnicodeError) as exc:
            raise DocumentServiceError("invalid_document_content", 422) from exc
        audiences = sorted(set(body.audiences))
        if not set(audiences) <= ALLOWED_AUDIENCES:
            raise DocumentServiceError("invalid_audience", 422)
        source_locator = body.source_locator.strip()
        if not source_locator:
            raise DocumentServiceError("source_locator_required", 422)
        effective_at = _utc(body.effective_at)
        expires_at = _utc(body.expires_at) if body.expires_at else None
        if expires_at is not None and expires_at <= effective_at:
            raise DocumentServiceError("invalid_effective_window", 422)
        metadata = {
            "title": document.title,
            "filename": parsed.filename,
            "format": parsed.format,
            "source_locator": source_locator,
            "attributes": body.metadata,
        }
        # Detach caller-owned dictionaries and normalize them to their JSON representation.
        try:
            metadata = json.loads(json.dumps(metadata, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise DocumentServiceError("invalid_metadata", 422) from exc
        digest = metadata_hash(metadata, audiences, effective_at, expires_at)
        return {
            "normalized_text": parsed.text,
            "content_hash": parsed.content_hash,
            "metadata_hash": digest,
            "metadata_json": metadata,
            "audiences": audiences,
            "effective_at": effective_at,
            "expires_at": expires_at,
        }

    async def create_version(
        self, document_id: UUID, body: VersionInput, *, force_new: bool = False
    ) -> tuple[DocumentVersion, bool]:
        """Create a draft; identical latest non-rejected/non-revoked content is reusable."""
        document = await self.lock_document(document_id)
        values = self._prepare(document, body)
        latest = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.tenant_id == self.actor.tenant_id,
            )
            .order_by(DocumentVersion.revision.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if (
            not force_new
            and latest is not None
            and latest.revoked_at is None
            and latest.review_status in {"draft", "pending", "approved"}
            and latest.content_hash == values["content_hash"]
            and latest.metadata_hash == values["metadata_hash"]
        ):
            verify_version_hashes(latest)
            return latest, False
        version = DocumentVersion(
            tenant_id=self.actor.tenant_id,
            kb_id=document.kb_id,
            document_id=document.id,
            revision=latest.revision + 1 if latest else 1,
            review_status="draft",
            **values,
        )
        self.session.add(version)
        await self.session.flush()
        record_audit(
            self.session,
            self.actor,
            event_type="version.created",
            entity_type="document_version",
            entity_id=version.id,
            reason="draft_created",
            old_ref=str(latest.id) if latest else None,
            new_ref=f"{version.content_hash}:{version.metadata_hash}",
        )
        await self.session.flush()
        return version, True

    async def update_draft(
        self, version_id: UUID, body: VersionInput, *, expected_row_version: int
    ) -> DocumentVersion:
        """Replace the entire editable draft; submitted content is immutable."""
        version = await self.lock_version(version_id)
        require_row_version(version, expected_row_version)
        if version.review_status != "draft":
            raise DocumentServiceError("version_frozen")
        document = await self.lock_document(version.document_id)
        values = self._prepare(document, body)
        old_ref = f"{version.content_hash}:{version.metadata_hash}"
        for key, value in values.items():
            setattr(version, key, value)
        version.row_version += 1
        record_audit(
            self.session,
            self.actor,
            event_type="version.updated",
            entity_type="document_version",
            entity_id=version.id,
            reason="draft_replaced",
            old_ref=old_ref,
            new_ref=f"{version.content_hash}:{version.metadata_hash}",
        )
        await self.session.flush()
        return version

    async def submit_for_review(
        self, version_id: UUID, *, expected_row_version: int
    ) -> DocumentVersion:
        version = await self.lock_version(version_id)
        require_row_version(version, expected_row_version)
        if version.review_status != "draft":
            raise DocumentServiceError("version_not_draft")
        if not version.audiences:
            raise DocumentServiceError("audience_required", 422)
        verify_version_hashes(version)
        version.review_status = "pending"
        version.row_version += 1
        record_audit(
            self.session,
            self.actor,
            event_type="version.submitted",
            entity_type="document_version",
            entity_id=version.id,
            reason="submitted_for_review",
            old_ref="draft",
            new_ref=f"pending:{version.content_hash}:{version.metadata_hash}",
        )
        await self.session.flush()
        return version
