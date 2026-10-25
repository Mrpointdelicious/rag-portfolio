"""Scene review and reader-visible active catalog. No patient or medical scoping."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from rag_portfolio.api.auth import Principal, authenticate, require_admin
from rag_portfolio.api.dependencies import session
from rag_portfolio.api.knowledge import actor
from rag_portfolio.contracts import Contract
from rag_portfolio.db.models import SceneSnapshot, SceneSpace
from rag_portfolio.services.documents import DocumentServiceError
from rag_portfolio.services.scene_catalog import (
    approve,
    catalog_hash,
    nodes,
    propose,
    published_catalogs,
)

router = APIRouter(prefix="/v1/scene-catalogs", tags=["Scene catalogs"])
Admin = Annotated[Principal, Depends(require_admin)]
Reader = Annotated[Principal, Depends(authenticate)]
Session = Annotated[object, Depends(session)]


class DraftRequest(Contract):
    kb_id: UUID
    source_version_id: UUID
    external_space_id: str | None = Field(default=None, min_length=1, max_length=160)
    display_name: str | None = Field(default=None, min_length=1, max_length=300)


class ApproveRequest(Contract):
    expected_catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=1000)


@router.get("")
async def published(principal: Reader, db: Session):
    return await published_catalogs(db, principal)


async def view(db, snapshot, tenant_id):
    if snapshot is None or snapshot.tenant_id != tenant_id:
        raise DocumentServiceError("scene_snapshot_not_found", 404)
    rows = await nodes(db, snapshot.id)
    space = await db.get(SceneSpace, snapshot.space_id)
    previous = (
        {
            n.name: n.floor
            for n in await nodes(db, space.active_snapshot_id)
            if n.node_type == "room"
        }
        if space.active_snapshot_id
        else {}
    )
    current = {n.name: n.floor for n in rows if n.node_type == "room"}
    return {
        "id": snapshot.id,
        "status": snapshot.status,
        "source_hash": snapshot.source_hash,
        "catalog_hash": await catalog_hash(db, snapshot),
        "scenes": current,
        "diff": {
            "added": sorted(current.keys() - previous.keys()),
            "removed": sorted(previous.keys() - current.keys()),
            "changed": sorted(
                k for k in current.keys() & previous.keys() if current[k] != previous[k]
            ),
        },
    }


@router.post("/drafts")
async def draft(body: DraftRequest, request: Request, principal: Admin, db: Session):
    async with db.begin():
        snapshot = await propose(
            db,
            actor(request, principal),
            body.kb_id,
            body.source_version_id,
            body.external_space_id,
            body.display_name,
        )
        return await view(db, snapshot, principal.tenant_id)


@router.get("/drafts/{snapshot_id}")
async def get_draft(snapshot_id: UUID, principal: Admin, db: Session):
    return await view(db, await db.get(SceneSnapshot, snapshot_id), principal.tenant_id)


@router.post("/drafts/{snapshot_id}/approve")
async def approve_draft(
    snapshot_id: UUID, body: ApproveRequest, request: Request, principal: Admin, db: Session
):
    async with db.begin():
        snapshot = await approve(
            db, actor(request, principal), snapshot_id, body.expected_catalog_hash, body.reason
        )
        return await view(db, snapshot, principal.tenant_id)
