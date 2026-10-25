"""Build immutable release indexes; only a verified ready release can replace the active one."""

import asyncio
import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from rag_portfolio.adapters.embedding import EmbeddingAdapter
from rag_portfolio.adapters.ingestion import asset_path, atomic_write, canonical, digest
from rag_portfolio.adapters.llama_vector import SparseEncoder, vector_store
from rag_portfolio.adapters.vector import QdrantConnection
from rag_portfolio.db.models import (
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeRelease,
    ReleaseDocument,
    SourceDocument,
)
from rag_portfolio.services.dense_retrieval import build_embedding_revision
from rag_portfolio.services.documents import (
    DocumentServiceError,
    record_audit,
    verify_version_hashes,
)
from rag_portfolio.services.jobs import enqueue, require_job_lease


def eligible_conditions(tenant_id, now):
    return (
        DocumentVersion.tenant_id == tenant_id,
        DocumentVersion.review_status == "approved",
        DocumentVersion.revoked_at.is_(None),
        DocumentVersion.effective_at <= now,
        (DocumentVersion.expires_at.is_(None) | (DocumentVersion.expires_at > now)),
        SourceDocument.tenant_id == tenant_id,
        SourceDocument.lifecycle == "active",
        SourceDocument.deleted_at.is_(None),
    )


async def release_rows(session, release):
    return list(
        (
            await session.execute(
                select(DocumentChunk, DocumentVersion, SourceDocument)
                .join(DocumentVersion, DocumentVersion.id == DocumentChunk.version_id)
                .join(SourceDocument, SourceDocument.id == DocumentVersion.document_id)
                .join(
                    ReleaseDocument,
                    (ReleaseDocument.version_id == DocumentVersion.id)
                    & (ReleaseDocument.release_id == release.id),
                )
                .where(
                    DocumentChunk.tenant_id == release.tenant_id,
                    *eligible_conditions(release.tenant_id, datetime.now(UTC)),
                )
                .order_by(DocumentChunk.id)
            )
        ).all()
    )


def row_manifest(rows):
    return [
        {
            "id": str(chunk.id),
            "hash": chunk.content_hash,
            "version": str(version.id),
            "metadata_hash": version.metadata_hash,
            "chunker": chunk.chunker_version,
            "embedding_input_hash": digest(
                "\n".join([source.title, chunk.section_path, chunk.text]).encode()
            ),
        }
        for chunk, version, source in rows
    ]


async def create_release(session, settings, actor, kb_id, version_ids):
    kb = await session.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == actor.tenant_id)
        .with_for_update()
    )
    if kb is None:
        raise DocumentServiceError("knowledge_base_not_found", 404)
    versions = list(
        (
            await session.scalars(
                select(DocumentVersion)
                .join(SourceDocument, SourceDocument.id == DocumentVersion.document_id)
                .where(
                    DocumentVersion.id.in_(version_ids),
                    DocumentVersion.kb_id == kb_id,
                    *eligible_conditions(actor.tenant_id, datetime.now(UTC)),
                )
                .with_for_update(of=DocumentVersion)
            )
        ).all()
    )
    if len(versions) != len(set(version_ids)) or len({v.document_id for v in versions}) != len(
        versions
    ):
        raise DocumentServiceError("release_versions_ineligible_or_duplicate")
    for version in versions:
        verify_version_hashes(version)
    from rag_portfolio.services.scene_catalog import catalog_hash, for_release

    scene_snapshot = await for_release(session, kb, versions)
    number = 1 + (
        await session.scalar(
            select(func.max(KnowledgeRelease.release_no)).where(KnowledgeRelease.kb_id == kb.id)
        )
        or 0
    )
    release = KnowledgeRelease(
        tenant_id=actor.tenant_id,
        kb_id=kb.id,
        scene_snapshot_id=scene_snapshot.id if scene_snapshot else None,
        release_no=number,
        base_release_id=kb.active_release_id,
        manifest_hash="0" * 64,
        collection_name=f"rag_{kb.id.hex}_r{number}",
        embedding_revision=build_embedding_revision(settings),
        reranker_revision=settings.rerank_model,
        chunker_version="llama-release-v1",
        requested_effective_at=datetime.now(UTC),
        manifest_json={},
    )
    session.add(release)
    await session.flush()
    session.add_all(
        [
            ReleaseDocument(
                tenant_id=actor.tenant_id,
                release_id=release.id,
                kb_id=kb.id,
                document_id=version.document_id,
                version_id=version.id,
            )
            for version in versions
        ]
    )
    await session.flush()
    rows = await release_rows(session, release)
    if not rows or {version.id for _, version, _ in rows} != set(version_ids):
        raise DocumentServiceError("release_missing_chunks")
    manifest = {
        "schema": 1,
        "scene_snapshot_id": str(scene_snapshot.id) if scene_snapshot else None,
        "scene_catalog_hash": await catalog_hash(session, scene_snapshot)
        if scene_snapshot
        else None,
        "chunks": row_manifest(rows),
        "embedding_revision": release.embedding_revision,
        "sparse": asdict(SparseEncoder.build([c.text for c, _, _ in rows])),
    }
    release.manifest_hash = digest(canonical(manifest))
    release.manifest_json = manifest
    job, _ = await enqueue(
        session,
        tenant_id=actor.tenant_id,
        kind="release.build",
        dedupe_key=f"release:{release.id}",
        payload={"release_id": str(release.id)},
    )
    record_audit(
        session,
        actor,
        event_type="release.requested",
        entity_type="kb_release",
        entity_id=release.id,
        reason="build_published_document_selection",
    )
    return release, job


async def verify_index(client, release):
    expected = {
        row["id"]: (row["hash"], row["version"], row["embedding_input_hash"])
        for row in release.manifest_json["chunks"]
    }
    seen, offset = {}, None
    while True:
        points, offset = await client.scroll(
            release.collection_name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if payload.get("tenant_id") != release.tenant_id or payload.get("release_id") != str(
                release.id
            ):
                raise DocumentServiceError("index_scope_mismatch")
            if payload.get("embedding_revision") != release.embedding_revision:
                raise DocumentServiceError("index_embedding_revision_mismatch")
            seen[str(point.id)] = (
                payload.get("content_hash"),
                payload.get("version_id"),
                payload.get("embedding_input_hash"),
            )
        if offset is None:
            break
    if expected != seen:
        raise DocumentServiceError("index_manifest_mismatch")


async def cached_embeddings(embedding, settings, revision, texts):
    vectors, missing = [None] * len(texts), []
    for index, text in enumerate(texts):
        key = digest(canonical([revision, text]))
        path = asset_path(settings, f"embeddings/{key}.json")
        if path.exists():
            saved = json.loads(path.read_bytes())
            values = saved.get("vector", [])
            if (
                saved.get("key") != key
                or len(values) != settings.embedding_dimension
                or not all(type(value) in (int, float) and math.isfinite(value) for value in values)
            ):
                raise DocumentServiceError("embedding_cache_corrupt")
            vectors[index] = values
        else:
            missing.append((index, key, path))
    if missing:
        values = await embedding.embed_documents([texts[index] for index, _, _ in missing])
        for (index, key, path), vector in zip(missing, values, strict=True):
            vectors[index] = vector
            await asyncio.to_thread(atomic_write, path, canonical({"key": key, "vector": vector}))
    return vectors


async def build_release(database, settings, job, *, progress=None):
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode

    rid = UUID(job.payload["release_id"])
    async with database.sessions.begin() as session:
        await require_job_lease(session, job)
        release = await session.scalar(
            select(KnowledgeRelease)
            .where(KnowledgeRelease.id == rid, KnowledgeRelease.tenant_id == job.tenant_id)
            .with_for_update()
        )
        if release is None or release.status not in {"queued", "building", "failed", "ready"}:
            raise DocumentServiceError("release_not_buildable")
        rows = await release_rows(session, release)
        if (
            digest(canonical(release.manifest_json)) != release.manifest_hash
            or row_manifest(rows) != release.manifest_json["chunks"]
            or release.embedding_revision != build_embedding_revision(settings)
        ):
            raise DocumentServiceError("release_manifest_drift")
        already_ready = release.status == "ready"
        if not already_ready:
            release.status = "building"
    vector = QdrantConnection(settings)
    embedding = EmbeddingAdapter(settings)
    try:
        store = vector_store(
            vector.client, release.collection_name, SparseEncoder(**release.manifest_json["sparse"])
        )
        for start in range(0, 0 if already_ready else len(rows), 8):
            if progress:
                await progress("indexing", completed=start, total=len(rows))
            nodes = []
            batch = rows[start : start + 8]
            if any(digest(chunk.text.encode()) != chunk.content_hash for chunk, _, _ in batch):
                raise DocumentServiceError("chunk_hash_mismatch")
            texts = [
                "\n".join([source.title, chunk.section_path, chunk.text])
                for chunk, _, source in batch
            ]
            vectors = await cached_embeddings(
                embedding, settings, release.embedding_revision, texts
            )
            for (chunk, version, source), text, values in zip(batch, texts, vectors, strict=True):
                metadata = {
                    "tenant_id": job.tenant_id,
                    "kb_id": str(release.kb_id),
                    "release_id": str(release.id),
                    "version_id": str(version.id),
                    "source_document_id": str(source.id),
                    "content_hash": chunk.content_hash,
                    "embedding_revision": release.embedding_revision,
                    "embedding_input_hash": digest(text.encode()),
                }
                nodes.append(
                    TextNode(
                        id_=str(chunk.id),
                        text=chunk.text,
                        embedding=values,
                        metadata=metadata,
                        relationships={
                            NodeRelationship.SOURCE: RelatedNodeInfo(node_id=str(version.id))
                        },
                        excluded_embed_metadata_keys=list(metadata),
                        excluded_llm_metadata_keys=list(metadata),
                    )
                )
            await store.async_add(nodes)
        if progress:
            await progress("verifying_index", total=len(rows))
        await verify_index(vector.client, release)
        async with database.sessions.begin() as session:
            await require_job_lease(session, job)
            current = await session.get(KnowledgeRelease, rid, with_for_update=True)
            fresh = await release_rows(session, current)
            if row_manifest(fresh) != release.manifest_json["chunks"]:
                raise DocumentServiceError("release_changed_during_build")
            if current.status == "building":
                current.status = "ready"
            elif current.status not in {"ready", "active", "retired"}:
                raise DocumentServiceError("release_state_changed_during_build")
        return {
            "release_id": str(rid),
            "status": "ready",
            "chunks": len(rows),
            "collection": release.collection_name,
            "manifest_hash": release.manifest_hash,
        }
    finally:
        await vector.close()
        await embedding.close()


async def activate_release(session, actor, release_id, reason, *, rollback=False):
    release = await session.scalar(
        select(KnowledgeRelease).where(
            KnowledgeRelease.id == release_id, KnowledgeRelease.tenant_id == actor.tenant_id
        )
    )
    if release is None:
        raise DocumentServiceError("release_not_found", 404)
    # Always lock KB before release, consistent with create_release.
    kb = await session.get(KnowledgeBase, release.kb_id, with_for_update=True)
    await session.refresh(release, with_for_update=True)
    if release.status not in ({"retired"} if rollback else {"ready"}):
        raise DocumentServiceError("release_not_activatable")
    rows = await release_rows(session, release)
    if row_manifest(rows) != release.manifest_json["chunks"]:
        raise DocumentServiceError("release_content_no_longer_eligible")
    if not rollback and kb.active_release_id != release.base_release_id:
        raise DocumentServiceError("release_base_conflict")
    from rag_portfolio.services.scene_catalog import activate as activate_catalog

    await activate_catalog(session, release, kb)
    previous = kb.active_release_id
    if previous:
        old = await session.get(KnowledgeRelease, previous, with_for_update=True)
        old.status, old.retired_at = "retired", datetime.now(UTC)
        await session.flush()
    release.status, release.activated_at = "active", datetime.now(UTC)
    release.retired_at = None
    kb.active_release_id, kb.enabled = release.id, True
    kb.row_version += 1
    record_audit(
        session,
        actor,
        event_type="release.rollback" if rollback else "release.activated",
        entity_type="kb_release",
        entity_id=release.id,
        reason=reason,
        old_ref=str(previous) if previous else None,
        new_ref=str(release.id),
    )
    return release
