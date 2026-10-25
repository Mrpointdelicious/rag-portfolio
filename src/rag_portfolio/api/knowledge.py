"""Document ingestion, chunk inspection, review, and release lifecycle on the main API."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from rag_portfolio.api.auth import Principal, require_admin
from rag_portfolio.api.dependencies import session
from rag_portfolio.contracts import JobView, ReleaseRequest, ReviewRequest, TransitionRequest
from rag_portfolio.db.models import (
    BackgroundJob,
    DocumentChunk,
    DocumentVersion,
    KnowledgeRelease,
)
from rag_portfolio.services.documents import ActorContext, DocumentServiceError, record_audit
from rag_portfolio.services.ingestion import register_source
from rag_portfolio.services.releases import activate_release, create_release, verify_index
from rag_portfolio.services.reviews import ReviewService
from rag_portfolio.services.worker_status import require_processing_ready

router = APIRouter(prefix="/v1", tags=["Knowledge lifecycle"])
Admin = Annotated[Principal, Depends(require_admin)]
Session = Annotated[object, Depends(session)]


def actor(request, principal):
    return ActorContext(principal.tenant_id, principal.actor_id, request.state.request_id)


def version_view(version):
    return {
        "id": version.id,
        "document_id": version.document_id,
        "kb_id": version.kb_id,
        "revision": version.revision,
        "row_version": version.row_version,
        "review_status": version.review_status,
        "content_hash": version.content_hash,
        "metadata_hash": version.metadata_hash,
        "audiences": version.audiences,
        "revoked_at": version.revoked_at,
    }


def release_view(release):
    return {
        "id": release.id,
        "kb_id": release.kb_id,
        "release_no": release.release_no,
        "base_release_id": release.base_release_id,
        "status": release.status,
        "manifest_hash": release.manifest_hash,
        "collection": release.collection_name,
        "chunks": len(release.manifest_json.get("chunks", [])),
        "activated_at": release.activated_at,
    }


@router.post("/knowledge-bases/{kb_id}/documents", response_model=JobView, status_code=202)
async def upload_document(
    kb_id: UUID,
    request: Request,
    principal: Admin,
    db: Session,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    title: Annotated[str, Query(min_length=1, max_length=500)],
    external_key: Annotated[str, Query(min_length=1, max_length=500)],
    audiences: str = "internal",
):
    """Upload raw PDF/TXT/Markdown bytes. Completion is reported by GET /jobs/{id}."""
    require_processing_ready(request.app.state.settings)
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > request.app.state.settings.ingestion_max_bytes:
            raise DocumentServiceError("document_too_large", 413)
    async with db.begin():
        _, job = await register_source(
            db,
            request.app.state.settings,
            actor(request, principal),
            kb_id,
            bytes(raw),
            filename=filename,
            title=title,
            external_key=external_key,
            audiences=[value.strip() for value in audiences.split(",")],
        )
    return job


@router.get("/knowledge-bases/{kb_id}/versions")
async def versions(kb_id: UUID, principal: Admin, db: Session):
    rows = await db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.kb_id == kb_id, DocumentVersion.tenant_id == principal.tenant_id)
        .order_by(DocumentVersion.created_at.desc())
        .limit(1000)
    )
    return [version_view(row) for row in rows]


@router.get("/versions/{version_id}/chunks")
async def chunks(version_id: UUID, principal: Admin, db: Session):
    rows = await db.scalars(
        select(DocumentChunk)
        .where(
            DocumentChunk.version_id == version_id, DocumentChunk.tenant_id == principal.tenant_id
        )
        .order_by(DocumentChunk.ordinal)
    )
    return [
        {
            "id": row.id,
            "ordinal": row.ordinal,
            "text": row.text,
            "content_hash": row.content_hash,
            "section": row.section_path,
            "provenance": row.provenance_json,
        }
        for row in rows
    ]


@router.post("/versions/{version_id}/approve")
async def approve(
    version_id: UUID, body: ReviewRequest, request: Request, principal: Admin, db: Session
):
    async with db.begin():
        result = await ReviewService(db, actor(request, principal)).approve_version(
            version_id, **body.model_dump()
        )
    return version_view(result.version)


@router.post("/versions/{version_id}/revoke")
async def revoke(
    version_id: UUID, body: TransitionRequest, request: Request, principal: Admin, db: Session
):
    async with db.begin():
        version = await db.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id, DocumentVersion.tenant_id == principal.tenant_id
            )
            .with_for_update()
        )
        if version is None:
            raise DocumentServiceError("version_not_found", 404)
        version.revoked_at = datetime.now(UTC)
        version.row_version += 1
        record_audit(
            db,
            actor(request, principal),
            event_type="version.revoked",
            entity_type="document_version",
            entity_id=version.id,
            reason=body.reason,
        )
    return version_view(version)


@router.post("/knowledge-bases/{kb_id}/releases", status_code=202)
async def request_release(
    kb_id: UUID, body: ReleaseRequest, request: Request, principal: Admin, db: Session
):
    require_processing_ready(request.app.state.settings)
    async with db.begin():
        release, job = await create_release(
            db, request.app.state.settings, actor(request, principal), kb_id, body.version_ids
        )
    return {"release": release_view(release), "job_id": job.id}


@router.get("/knowledge-bases/{kb_id}/releases")
async def releases(kb_id: UUID, principal: Admin, db: Session):
    rows = await db.scalars(
        select(KnowledgeRelease)
        .where(KnowledgeRelease.kb_id == kb_id, KnowledgeRelease.tenant_id == principal.tenant_id)
        .order_by(KnowledgeRelease.release_no.desc())
        .limit(1000)
    )
    return [release_view(row) for row in rows]


@router.post("/releases/{release_id}/activate")
@router.post("/releases/{release_id}/rollback")
async def activate(
    release_id: UUID, body: TransitionRequest, request: Request, principal: Admin, db: Session
):
    async with db.begin():
        release = await db.scalar(
            select(KnowledgeRelease).where(
                KnowledgeRelease.id == release_id, KnowledgeRelease.tenant_id == principal.tenant_id
            )
        )
        if release is None:
            raise DocumentServiceError("release_not_found", 404)
        await verify_index(request.app.state.vector.client, release)
        result = await activate_release(
            db,
            actor(request, principal),
            release_id,
            body.reason,
            rollback=request.url.path.endswith("/rollback"),
        )
    return release_view(result)


@router.post("/jobs/{job_id}/retry", response_model=JobView)
async def retry(job_id: UUID, principal: Admin, db: Session):
    async with db.begin():
        job = await db.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == job_id, BackgroundJob.tenant_id == principal.tenant_id)
            .with_for_update()
        )
        if job is None or job.status != "failed":
            raise DocumentServiceError("job_not_retryable")
        job.status, job.attempts, job.finished_at = "queued", 0, None
        job.next_run_at, job.last_error_code = datetime.now(UTC), None
    return job
