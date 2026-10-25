"""First-class source registration and restartable ingestion into the business database."""

import asyncio
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import select

from rag_portfolio.adapters.ingestion import (
    ADAPTER_REVISION,
    asset_path,
    atomic_write,
    digest,
    parse_source,
    token_ids,
)
from rag_portfolio.db.models import BackgroundJob, DocumentChunk, SourceAsset
from rag_portfolio.services.documents import (
    ActorContext,
    DocumentService,
    DocumentServiceError,
    VersionInput,
    record_audit,
)
from rag_portfolio.services.jobs import enqueue, require_job_lease


async def register_source(
    session,
    settings,
    actor,
    kb_id,
    content,
    *,
    filename,
    title,
    external_key,
    audiences,
    effective_at=None,
):
    name = Path(filename.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".txt", ".md", ".markdown"}:
        raise DocumentServiceError("unsupported_document_format", 422)
    if not content or len(content) > settings.ingestion_max_bytes:
        raise DocumentServiceError("invalid_document_size", 413)
    if not audiences or not set(audiences) <= {"patient", "doctor", "internal"}:
        raise DocumentServiceError("invalid_audience", 422)
    sha = digest(content)
    key = f"sources/{actor.tenant_id.encode().hex()}/{sha}{suffix}"
    path = asset_path(settings, key)
    if not path.exists():
        await asyncio.to_thread(atomic_write, path, content)
    elif digest(path.read_bytes()) != sha:
        raise DocumentServiceError("source_asset_corrupt")
    service = DocumentService(session, actor)
    document, _ = await service.create_document(
        kb_id,
        title=title,
        source_instance_id="uploads",
        external_key=external_key,
    )
    asset = await session.scalar(
        select(SourceAsset).where(
            SourceAsset.tenant_id == actor.tenant_id, SourceAsset.storage_key == key
        )
    )
    if asset is None:
        asset = SourceAsset(
            tenant_id=actor.tenant_id,
            storage_key=key,
            sha256=sha,
            size_bytes=len(content),
            media_type="application/pdf" if suffix == ".pdf" else "text/plain",
            source_locator=name,
        )
        session.add(asset)
        await session.flush()
    payload = {
        "document_id": str(document.id),
        "asset_id": str(asset.id),
        "filename": name,
        "actor_id": actor.actor_id,
        "request_id": str(actor.request_id),
        "audiences": sorted(set(audiences)),
        # Unspecified effective date is stable across repeated uploads.
        "effective_at": effective_at or "2000-01-01T00:00:00+00:00",
        "chunk_size": settings.ingestion_chunk_size,
        "chunk_overlap": settings.ingestion_chunk_overlap,
        "ocr": settings.docling_ocr,
        "tables": settings.docling_tables,
        "adapter_revision": ADAPTER_REVISION,
        "model_revision": settings.docling_model_revision,
    }
    from rag_portfolio.adapters.ingestion import canonical

    key = "ingest:" + digest(canonical(payload | {"request_id": "", "actor_id": ""}))
    existing = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.tenant_id == actor.tenant_id,
            BackgroundJob.dedupe_key == key,
        )
    )
    if existing is not None:
        return document, existing
    job, _ = await enqueue(
        session,
        tenant_id=actor.tenant_id,
        kind="document.ingest",
        dedupe_key=key,
        payload=payload,
    )
    return document, job


async def ingest_document(database, settings, job, *, progress=None):
    payload = job.payload
    actor = ActorContext(job.tenant_id, payload["actor_id"], UUID(payload["request_id"]))
    async with database.sessions() as session:
        asset = await session.scalar(
            select(SourceAsset).where(
                SourceAsset.id == UUID(payload["asset_id"]), SourceAsset.tenant_id == job.tenant_id
            )
        )
        if asset is None:
            raise DocumentServiceError("source_asset_not_found", 404)
    parser_settings = settings.model_copy(
        update={
            "ingestion_chunk_size": payload["chunk_size"],
            "ingestion_chunk_overlap": payload["chunk_overlap"],
            "docling_ocr": payload["ocr"],
            "docling_tables": payload["tables"],
            "docling_model_revision": payload.get(
                "model_revision", settings.docling_model_revision
            ),
        }
    )
    if progress:
        await progress(
            "parsing", parser="docling" if payload["filename"].lower().endswith(".pdf") else "utf8"
        )
    parsed = await asyncio.to_thread(
        parse_source,
        parser_settings,
        asset_path(settings, asset.storage_key),
        payload["filename"],
        asset.sha256,
    )
    if progress:
        await progress("persisting_chunks", chunks=len(parsed.chunks), cache_hit=parsed.cache_hit)
    async with database.sessions.begin() as session:
        await require_job_lease(session, job)
        service = DocumentService(session, actor)
        version, created = await service.create_version(
            UUID(payload["document_id"]),
            VersionInput(
                filename=payload["filename"] + ".md",
                content=parsed.text,
                source_locator=asset.source_locator,
                effective_at=datetime.fromisoformat(payload["effective_at"]),
                audiences=payload["audiences"],
                metadata={
                    "ingestion_revision": parsed.revision,
                    "source_sha256": asset.sha256,
                    "original_filename": payload["filename"],
                },
            ),
        )
        if created:
            version.original_asset_id = asset.id
            for ordinal, chunk in enumerate(parsed.chunks):
                sha = digest(chunk.text.encode())
                session.add(
                    DocumentChunk(
                        id=uuid5(version.id, f"{parsed.revision}:{ordinal}:{sha}"),
                        tenant_id=job.tenant_id,
                        version_id=version.id,
                        chunker_version=parsed.revision,
                        ordinal=ordinal,
                        section_path=chunk.section,
                        text=chunk.text,
                        content_hash=sha,
                        token_count=len(token_ids(chunk.text)),
                        provenance_json=chunk.provenance,
                    )
                )
            await service.submit_for_review(version.id, expected_row_version=version.row_version)
            record_audit(
                session,
                actor,
                event_type="source.ingested",
                entity_type="document_version",
                entity_id=version.id,
                reason="docling_llamaindex_ingestion",
            )
    return {
        "document_id": payload["document_id"],
        "version_id": str(version.id),
        "chunks": len(parsed.chunks),
        "parser_revision": parsed.revision,
        "parse_cache_hit": parsed.cache_hit,
        "review_status": version.review_status,
    }
