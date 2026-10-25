"""Reviewed scene snapshots tied to the exact hospital document release."""

import re
from datetime import UTC, datetime

from sqlalchemy import func, select

from rag_portfolio.adapters.ingestion import canonical, digest
from rag_portfolio.db.models import (
    DocumentVersion,
    KnowledgeBase,
    KnowledgeRelease,
    ReleaseDocument,
    SceneNode,
    SceneSnapshot,
    SceneSpace,
    SourceDocument,
)
from rag_portfolio.services.documents import DocumentServiceError, record_audit


def derive_scenes(text):
    scenes, floor = {}, None
    floors = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

    def add(name, value):
        name = name.strip()
        if name:
            scenes[name] = value if name not in scenes or scenes[name] == value else None

    for line in text.splitlines():
        heading = re.match(r"^#{1,6}\s+\[通用\]\[([一二三四五六七八九0-9]+)楼\]\s*(.+)$", line)
        if heading:
            value, name = heading.groups()
            floor = floors.get(value, int(value) if value.isdigit() else None)
            add(name, floor)
            continue
        if floor is None or line.startswith(("问题：", "回答：")):
            continue
        layout = re.match(r"\[布局\]([^\s及]+?(?:大厅|中心))(?=及|\s|$)", line)
        if layout:
            add(layout[1], floor)
        for name in re.findall(r"(?:入口是|走廊是)([^\s，。]+)", line):
            add(name, floor)
        words = line.split()
        if words and all(
            re.fullmatch(
                r"[^\s\[\]，。；：]+(?:诊疗室|评估室|康复室|大厅|中心|展示区)[0-9]*(?:号)?", w
            )
            for w in words
        ):
            for name in words:
                add(name, floor)
    if not scenes:
        raise DocumentServiceError("scene_source_format_requires_review", 422)
    return scenes


async def nodes(session, snapshot_id):
    return list(
        await session.scalars(
            select(SceneNode)
            .where(SceneNode.snapshot_id == snapshot_id)
            .order_by(SceneNode.node_id)
        )
    )


async def catalog_hash(session, snapshot):
    return digest(
        canonical(
            {
                "source_hash": snapshot.source_hash,
                "nodes": [
                    {"id": n.node_id, "name": n.name, "floor": n.floor, "source": n.source_locator}
                    for n in await nodes(session, snapshot.id)
                ],
            }
        )
    )


async def propose(session, actor, kb_id, version_id, external_space_id=None, display_name=None):
    kb = await session.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == actor.tenant_id)
        .with_for_update()
    )
    version = await session.get(DocumentVersion, version_id)
    source = await session.get(SourceDocument, version.document_id) if version else None
    if (
        kb is None
        or kb.code != "hospital_kb"
        or version is None
        or version.kb_id != kb.id
        or version.review_status != "approved"
        or version.revoked_at is not None
        or source.external_key != "hospital-scene-guide"
    ):
        raise DocumentServiceError("scene_source_not_approved_hospital_guide", 422)
    parsed = derive_scenes(version.normalized_text)
    if kb.space_id:
        space = await session.get(SceneSpace, kb.space_id, with_for_update=True)
        if external_space_id and external_space_id != space.external_space_id:
            raise DocumentServiceError("scene_space_binding_conflict")
    else:
        if not external_space_id:
            raise DocumentServiceError("scene_space_registration_required", 422)
        space = SceneSpace(
            tenant_id=actor.tenant_id,
            hospital_id=kb.hospital_id or external_space_id,
            external_space_id=external_space_id,
        )
        session.add(space)
        await session.flush()
        kb.space_id = space.id
    old = await session.scalar(
        select(SceneSnapshot)
        .where(
            SceneSnapshot.space_id == space.id, SceneSnapshot.source_hash == version.content_hash
        )
        .order_by(SceneSnapshot.revision.desc())
    )
    if old:
        return old
    revision = 1 + (
        await session.scalar(
            select(func.max(SceneSnapshot.revision)).where(SceneSnapshot.space_id == space.id)
        )
        or 0
    )
    snapshot = SceneSnapshot(
        tenant_id=actor.tenant_id,
        space_id=space.id,
        revision=revision,
        base_snapshot_id=space.active_snapshot_id,
        upstream_catalog_version=revision,
        source_hash=version.content_hash,
    )
    session.add(snapshot)
    await session.flush()
    locator = version.metadata_json.get("source_locator", source.external_key)
    session.add(
        SceneNode(
            snapshot_id=snapshot.id,
            node_id="@hospital",
            node_type="hospital",
            name=display_name or external_space_id or space.external_space_id,
            floor=None,
            source_locator=locator,
        )
    )
    session.add_all(
        [
            SceneNode(
                snapshot_id=snapshot.id,
                node_id=name,
                parent_node_id="@hospital",
                node_type="room",
                name=name,
                floor=floor,
                source_locator=locator,
            )
            for name, floor in parsed.items()
        ]
    )
    await session.flush()
    record_audit(
        session,
        actor,
        event_type="scene_catalog.proposed",
        entity_type="scene_snapshot",
        entity_id=snapshot.id,
        reason="derive_from_approved_hospital_guide",
    )
    return snapshot


async def approve(session, actor, snapshot_id, expected_hash, reason):
    snapshot = await session.scalar(
        select(SceneSnapshot)
        .where(SceneSnapshot.id == snapshot_id, SceneSnapshot.tenant_id == actor.tenant_id)
        .with_for_update()
    )
    if snapshot is None:
        raise DocumentServiceError("scene_snapshot_not_found", 404)
    if snapshot.status != "draft" or await catalog_hash(session, snapshot) != expected_hash:
        raise DocumentServiceError("scene_snapshot_review_conflict")
    snapshot.status, snapshot.reviewed_by = "approved", actor.actor_id
    record_audit(
        session,
        actor,
        event_type="scene_catalog.approved",
        entity_type="scene_snapshot",
        entity_id=snapshot.id,
        reason=reason,
    )
    return snapshot


async def for_release(session, kb, versions):
    if not kb.space_id:
        return None
    guides = [
        v
        for v in versions
        if (await session.get(SourceDocument, v.document_id)).external_key == "hospital-scene-guide"
    ]
    if len(guides) != 1:
        raise DocumentServiceError("scene_source_required_for_hospital_release")
    snapshot = await session.scalar(
        select(SceneSnapshot)
        .where(
            SceneSnapshot.space_id == kb.space_id,
            SceneSnapshot.source_hash == guides[0].content_hash,
            SceneSnapshot.status.in_(["approved", "published", "retired"]),
        )
        .order_by(SceneSnapshot.revision.desc())
    )
    if snapshot is None:
        raise DocumentServiceError("scene_catalog_review_required")
    return snapshot


async def activate(session, release, kb):
    if not release.scene_snapshot_id:
        return
    snapshot = await session.get(SceneSnapshot, release.scene_snapshot_id, with_for_update=True)
    if snapshot is None or snapshot.space_id != kb.space_id:
        raise DocumentServiceError("scene_release_binding_invalid")
    if await catalog_hash(session, snapshot) != release.manifest_json.get("scene_catalog_hash"):
        raise DocumentServiceError("scene_catalog_manifest_drift")
    space = await session.get(SceneSpace, snapshot.space_id, with_for_update=True)
    if space.active_snapshot_id and space.active_snapshot_id != snapshot.id:
        previous = await session.get(SceneSnapshot, space.active_snapshot_id, with_for_update=True)
        previous.status = "retired"
    snapshot.status, snapshot.published_at = "published", datetime.now(UTC)
    space.active_snapshot_id, space.enabled = snapshot.id, True
    space.row_version += 1


async def published_catalogs(session, principal):
    from rag_portfolio.services.releases import eligible_conditions

    statement = (
        select(KnowledgeBase, KnowledgeRelease, SceneSpace, SceneSnapshot)
        .join(KnowledgeRelease, KnowledgeRelease.id == KnowledgeBase.active_release_id)
        .join(SceneSpace, SceneSpace.id == KnowledgeBase.space_id)
        .join(SceneSnapshot, SceneSnapshot.id == KnowledgeRelease.scene_snapshot_id)
        .where(
            KnowledgeBase.tenant_id == principal.tenant_id,
            KnowledgeBase.enabled.is_(True),
            KnowledgeRelease.status == "active",
            SceneSpace.enabled.is_(True),
            SceneSnapshot.status == "published",
            SceneSpace.active_snapshot_id == SceneSnapshot.id,
        )
    )
    if not principal.admin:
        statement = statement.where(KnowledgeBase.scope_key.in_(principal.scope_keys))
    result = {}
    for _kb, release, space, snapshot in (await session.execute(statement)).all():
        vquery = (
            select(DocumentVersion)
            .join(SourceDocument, SourceDocument.id == DocumentVersion.document_id)
            .join(ReleaseDocument, ReleaseDocument.version_id == DocumentVersion.id)
            .where(
                ReleaseDocument.release_id == release.id,
                DocumentVersion.content_hash == snapshot.source_hash,
                *eligible_conditions(principal.tenant_id, datetime.now(UTC)),
            )
        )
        if not principal.admin:
            vquery = vquery.where(DocumentVersion.audiences.contains([principal.audience]))
        version = await session.scalar(vquery)
        if version is None or await catalog_hash(session, snapshot) != release.manifest_json.get(
            "scene_catalog_hash"
        ):
            continue
        rows = await nodes(session, snapshot.id)
        result[space.external_space_id] = {
            "name": next(n.name for n in rows if n.node_type == "hospital"),
            "snapshot_id": str(snapshot.id),
            "release_id": str(release.id),
            "source_version_id": str(version.id),
            "source_locator": rows[0].source_locator,
            "catalog_hash": await catalog_hash(session, snapshot),
            "scenes": {n.name: n.floor for n in rows if n.node_type == "room"},
        }
    return result
