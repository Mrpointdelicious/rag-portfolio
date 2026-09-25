"""External Markdown/TXT import: manifest, preflight checks, per-document transactions.

The CLI (tools/import_documents.py) owns database lifecycle and console output; this
module stays importable and testable without a running database. Imports stop at
``pending``; review, chunking and indexing belong to later stages.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_portfolio.db.models import DocumentVersion, KnowledgeBase, SourceDocument
from rag_portfolio.services.chunking import MAX_DOCUMENT_BYTES, parse_document
from rag_portfolio.services.documents import (
    ALLOWED_AUDIENCES,
    ActorContext,
    DocumentService,
    VersionInput,
)

IMPORT_ACTOR_ID = "import_cli"
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}

Error = tuple[str, str]  # (code, message)


class ManifestError(ValueError):
    """Manifest-level problems; the whole batch must stop."""

    def __init__(self, errors: list[Error]):
        self.errors = errors
        super().__init__("; ".join(f"{code}: {message}" for code, message in errors))


@dataclass(frozen=True)
class Manifest:
    kb_id: UUID
    tenant_id: str
    source_instance_id: str
    defaults: dict[str, Any]
    documents: list[dict[str, Any]]
    path: Path


@dataclass(frozen=True)
class DocumentConfig:
    external_key: str
    title: str
    filename: str
    source_locator: str
    audiences: list[str]
    effective_at: datetime
    expires_at: datetime | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FileCheck:
    ok: bool
    content: bytes | None = None
    error_code: str | None = None
    message: str | None = None


@dataclass
class ImportRecord:
    external_key: str
    filename: str
    status: str  # "success" | "failed"
    document_created: bool | None = None
    document_id: UUID | None = None
    version_created: bool | None = None
    version_id: UUID | None = None
    revision: int | None = None
    review_status: str | None = None
    content_hash: str | None = None
    metadata_hash: str | None = None
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class StatusRow:
    external_key: str
    document: str  # "found" | "not imported"
    revision: int | None
    review_status: str | None


@dataclass
class ImportReport:
    started_at: datetime
    finished_at: datetime
    kb_id: UUID
    tenant_id: str
    source_instance_id: str
    records: list[ImportRecord] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.records),
            "succeeded": sum(1 for record in self.records if record.status == "success"),
            "failed": sum(1 for record in self.records if record.status == "failed"),
            "documents_created": sum(1 for record in self.records if record.document_created),
            "documents_reused": sum(
                1 for record in self.records if record.document_created is False
            ),
            "versions_created": sum(1 for record in self.records if record.version_created),
            "versions_reused": sum(1 for record in self.records if record.version_created is False),
        }


def _text(value: Any, errors: list[Error], field: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append((f"{field}_required", f"{field} must be a non-empty string"))
        return None
    return value.strip()


def _uuid(value: Any, errors: list[Error], field: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        errors.append((f"{field}_invalid", f"{field} must be a valid UUID"))
        return None


def _datetime(value: Any, errors: list[Error], field: str, *, required: bool) -> datetime | None:
    if value is None:
        if required:
            message = f"{field} is required and must be an ISO 8601 timestamp with timezone"
            errors.append((f"{field}_required", message))
        return None
    if not isinstance(value, str):
        errors.append(("invalid_datetime", f"{field} must be an ISO 8601 string with timezone"))
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        errors.append(("invalid_datetime", f"{field} is not a valid ISO 8601 timestamp: {value!r}"))
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(("timezone_required", f"{field} must include a timezone offset: {value!r}"))
        return None
    return parsed


def load_manifest(path: Path) -> Manifest:
    """Parse and structurally validate a manifest; raises ManifestError on problems."""
    errors: list[Error] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        message = f"manifest is not valid JSON: {exc}"
        raise ManifestError([("manifest_invalid_json", message)]) from exc
    except OSError as exc:
        raise ManifestError([("manifest_unreadable", f"cannot read manifest: {exc}")]) from exc
    if not isinstance(raw, dict):
        raise ManifestError([("manifest_invalid", "manifest root must be a JSON object")])
    kb_id = _uuid(raw.get("kb_id"), errors, "kb_id")
    tenant_id = _text(raw.get("tenant_id"), errors, "tenant_id")
    source_instance_id = _text(raw.get("source_instance_id"), errors, "source_instance_id")
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        errors.append(("invalid_defaults", "defaults must be a JSON object"))
    documents = raw.get("documents")
    if not isinstance(documents, list) or not documents:
        errors.append(("documents_required", "documents must be a non-empty array"))
        documents = []
    seen: dict[str, int] = {}
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            errors.append(("invalid_document_entry", f"documents[{index}] must be a JSON object"))
            continue
        key = document.get("external_key")
        if not isinstance(key, str) or not key.strip():
            errors.append(
                (
                    "external_key_required",
                    f"documents[{index}].external_key must be a non-empty string",
                )
            )
            continue
        if key in seen:
            errors.append(
                (
                    "duplicate_external_key",
                    f"external_key {key!r} is duplicated at "
                    f"documents[{seen[key]}] and documents[{index}]",
                )
            )
        seen[key] = index
    if errors:
        raise ManifestError(errors)
    assert kb_id is not None and tenant_id is not None and source_instance_id is not None
    return Manifest(kb_id, tenant_id, source_instance_id, defaults, documents, path.resolve())


def merge_document_config(defaults: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Merge defaults with per-document overrides; document-level keys win.

    ``expires_at`` follows key presence, so an explicit document-level null clears a
    default. Metadata dictionaries merge shallowly, document-level keys win.
    """
    merged = dict(defaults)
    for key in ("audiences", "effective_at", "expires_at"):
        if key in document:
            merged[key] = document[key]
    default_metadata = defaults.get("metadata")
    document_metadata = document.get("metadata")
    if isinstance(default_metadata, dict) and isinstance(document_metadata, dict):
        merged["metadata"] = {**default_metadata, **document_metadata}
    elif isinstance(document_metadata, dict):
        merged["metadata"] = dict(document_metadata)
    else:
        merged["metadata"] = document_metadata if "metadata" in document else default_metadata or {}
    return merged


def build_document_config(
    document: dict[str, Any], merged: dict[str, Any]
) -> tuple[DocumentConfig | None, list[Error]]:
    """Validate one merged document entry; returns (config, errors)."""
    errors: list[Error] = []
    title = _text(document.get("title"), errors, "title")
    filename = _text(document.get("filename"), errors, "filename")
    source_locator = _text(document.get("source_locator"), errors, "source_locator")
    audiences = merged.get("audiences")
    if not isinstance(audiences, list) or not all(isinstance(item, str) for item in audiences):
        errors.append(("invalid_audience", "audiences must be an array of strings"))
    elif not audiences:
        errors.append(("audience_required", "audiences must contain at least one audience"))
    else:
        invalid = sorted(set(audiences) - ALLOWED_AUDIENCES)
        if invalid:
            allowed = ", ".join(sorted(ALLOWED_AUDIENCES))
            errors.append(
                ("invalid_audience", f"audiences contains {invalid!r}; allowed: {allowed}")
            )
    effective_at = _datetime(merged.get("effective_at"), errors, "effective_at", required=True)
    expires_at = _datetime(merged.get("expires_at"), errors, "expires_at", required=False)
    if effective_at is not None and expires_at is not None and expires_at <= effective_at:
        errors.append(("invalid_effective_window", "expires_at must be later than effective_at"))
    metadata = merged.get("metadata")
    if not isinstance(metadata, dict):
        errors.append(("invalid_metadata", "metadata must be a JSON object"))
    else:
        try:
            json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            errors.append(("invalid_metadata", "metadata must be JSON serializable"))
    if errors or title is None or filename is None or source_locator is None:
        return None, errors
    assert effective_at is not None and isinstance(audiences, list) and isinstance(metadata, dict)
    return (
        DocumentConfig(
            external_key=document["external_key"],
            title=title,
            filename=filename,
            source_locator=source_locator,
            audiences=list(audiences),
            effective_at=effective_at,
            expires_at=expires_at,
            metadata=dict(metadata),
        ),
        [],
    )


def check_document_file(manifest: Manifest, filename: str) -> FileCheck:
    """Validate one file under ``documents/``; parse_document is the final gate."""
    documents_dir = (manifest.path.parent / "documents").resolve()
    resolved = (documents_dir / filename).resolve()
    if not resolved.is_relative_to(documents_dir):
        return FileCheck(
            False,
            error_code="path_traversal",
            message=f"filename escapes the documents directory: {filename!r}",
        )
    if not resolved.is_file():
        return FileCheck(
            False,
            error_code="file_not_found",
            message=f"file not found: documents/{filename}",
        )
    if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
        return FileCheck(
            False,
            error_code="unsupported_extension",
            message=(
                f"unsupported extension {resolved.suffix!r}: "
                "only .md, .markdown and .txt are supported"
            ),
        )
    if resolved.stat().st_size > MAX_DOCUMENT_BYTES:
        return FileCheck(False, error_code="file_too_large", message="file exceeds the 2 MiB limit")
    try:
        content = resolved.read_bytes()
    except OSError as exc:
        return FileCheck(False, error_code="file_unreadable", message=f"cannot read file: {exc}")
    try:
        parse_document(filename, content)
    except ValueError as exc:
        return FileCheck(False, error_code="invalid_document_content", message=str(exc))
    return FileCheck(True, content=content)


async def check_knowledge_base(session: AsyncSession, manifest: Manifest) -> list[Error]:
    """Read-only check: the knowledge base exists and belongs to the manifest tenant."""
    kb = await session.get(KnowledgeBase, manifest.kb_id)
    if kb is None:
        return [("knowledge_base_not_found", f"knowledge base {manifest.kb_id} does not exist")]
    if kb.tenant_id != manifest.tenant_id:
        return [
            (
                "knowledge_base_tenant_mismatch",
                f"knowledge base belongs to tenant {kb.tenant_id!r}, "
                f"manifest declares {manifest.tenant_id!r}",
            )
        ]
    return []


async def import_one_document(
    session: AsyncSession, manifest: Manifest, config: DocumentConfig, content: bytes
) -> ImportRecord:
    """Import one document inside the caller's transaction; exceptions escape for rollback."""
    service = DocumentService(
        session,
        ActorContext(tenant_id=manifest.tenant_id, actor_id=IMPORT_ACTOR_ID, request_id=uuid4()),
    )
    document, document_created = await service.create_document(
        manifest.kb_id,
        title=config.title,
        source_instance_id=manifest.source_instance_id,
        external_key=config.external_key,
        source_system="upload",
    )
    version, version_created = await service.create_version(
        document.id,
        VersionInput(
            filename=config.filename,
            content=content,
            source_locator=config.source_locator,
            effective_at=config.effective_at,
            audiences=config.audiences,
            expires_at=config.expires_at,
            metadata=config.metadata,
        ),
    )
    if version.review_status == "draft":
        version = await service.submit_for_review(
            version.id, expected_row_version=version.row_version
        )
    return ImportRecord(
        external_key=config.external_key,
        filename=config.filename,
        status="success",
        document_created=document_created,
        document_id=document.id,
        version_created=version_created,
        version_id=version.id,
        revision=version.revision,
        review_status=version.review_status,
        content_hash=version.content_hash,
        metadata_hash=version.metadata_hash,
    )


async def collect_status(session: AsyncSession, manifest: Manifest) -> list[StatusRow]:
    """Read-only view of each manifest entry: document presence and latest version."""
    documents = (
        await session.scalars(
            select(SourceDocument).where(
                SourceDocument.kb_id == manifest.kb_id,
                SourceDocument.tenant_id == manifest.tenant_id,
                SourceDocument.source_instance_id == manifest.source_instance_id,
            )
        )
    ).all()
    by_key = {document.external_key: document for document in documents}
    rows: list[StatusRow] = []
    for entry in manifest.documents:
        key = entry["external_key"]
        document = by_key.get(key)
        if document is None:
            rows.append(StatusRow(key, "not imported", None, None))
            continue
        latest = await session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.revision.desc())
            .limit(1)
        )
        rows.append(
            StatusRow(
                key,
                "found",
                latest.revision if latest else None,
                latest.review_status if latest else None,
            )
        )
    return rows


def _record_dict(record: ImportRecord) -> dict[str, Any]:
    value: dict[str, Any] = {
        "external_key": record.external_key,
        "filename": record.filename,
        "status": record.status,
    }
    if record.status == "success":
        value.update(
            {
                "document_created": record.document_created,
                "document_id": str(record.document_id),
                "version_created": record.version_created,
                "version_id": str(record.version_id),
                "revision": record.revision,
                "review_status": record.review_status,
                "content_hash": record.content_hash,
                "metadata_hash": record.metadata_hash,
            }
        )
    else:
        value.update({"error_code": record.error_code, "message": record.message})
    return value


def write_report(path: Path, report: ImportReport) -> None:
    """Write the report atomically via a temporary file in the same directory."""
    payload = {
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "kb_id": str(report.kb_id),
        "tenant_id": report.tenant_id,
        "source_instance_id": report.source_instance_id,
        "summary": report.summary(),
        "documents": [_record_dict(record) for record in report.records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=".import_report-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
