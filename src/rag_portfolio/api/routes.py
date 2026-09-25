import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rag_portfolio.api.auth import Principal, authenticate, require_admin
from rag_portfolio.api.dependencies import session
from rag_portfolio.contracts import (
    JobView,
    KnowledgeBaseCreate,
    KnowledgeBaseView,
    RetrieveRequest,
    RetrieveResponse,
)
from rag_portfolio.db.models import AuditEvent, BackgroundJob, KnowledgeBase
from rag_portfolio.services.jobs import enqueue

health = APIRouter(tags=["Health"])
router = APIRouter(prefix="/v1")
Reader = Annotated[Principal, Depends(authenticate)]
Admin = Annotated[Principal, Depends(require_admin)]
Session = Annotated[AsyncSession, Depends(session)]


@health.get("/health/live")
async def live():
    return {"status": "ok", "service": "rag-portfolio"}


@health.get("/health/ready")
async def ready(request: Request):
    async def postgres():
        async with request.app.state.database.sessions() as connection:
            revisions = (
                await connection.scalars(text("SELECT version_num FROM alembic_version"))
            ).all()
            if revisions != ["0001"]:
                raise RuntimeError("schema_mismatch")

    async def check(operation):
        try:
            async with asyncio.timeout(request.app.state.settings.dependency_timeout_seconds):
                await operation()
            return "ok"
        except Exception:
            return "unavailable"

    results = await asyncio.gather(check(postgres), check(request.app.state.vector.check))
    checks = dict(zip(["postgres_schema", "qdrant"], results, strict=True))
    healthy = all(value == "ok" for value in results)
    return JSONResponse(
        {"status": "ready" if healthy else "not_ready", "checks": checks},
        status_code=200 if healthy else 503,
    )


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseView], tags=["Knowledge Bases"])
async def list_bases(
    principal: Reader,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    statement = select(KnowledgeBase).where(KnowledgeBase.tenant_id == principal.tenant_id)
    if not principal.admin:
        statement = statement.where(KnowledgeBase.scope_key.in_(principal.scope_keys))
    return (
        await db.scalars(statement.order_by(KnowledgeBase.id).limit(limit).offset(offset))
    ).all()


@router.post(
    "/knowledge-bases", response_model=KnowledgeBaseView, status_code=201, tags=["Knowledge Bases"]
)
async def create_base(body: KnowledgeBaseCreate, request: Request, principal: Admin, db: Session):
    try:
        async with db.begin():
            kb = KnowledgeBase(tenant_id=principal.tenant_id, **body.model_dump())
            db.add(kb)
            await db.flush()
            db.add(
                AuditEvent(
                    tenant_id=principal.tenant_id,
                    actor_id=principal.actor_id,
                    event_type="knowledge_base.created",
                    entity_type="knowledge_base",
                    entity_id=kb.id,
                    reason="initial_registration",
                    request_id=request.state.request_id,
                )
            )
        return kb
    except IntegrityError:
        raise HTTPException(409, "knowledge_base_conflict_or_invalid_space") from None


@router.post("/jobs/probes", response_model=JobView, status_code=202, tags=["Jobs"])
async def create_probe(
    request: Request,
    principal: Admin,
    db: Session,
    idempotency_key: Annotated[UUID, Header()],
):
    async with db.begin():
        job, created = await enqueue(
            db,
            tenant_id=principal.tenant_id,
            kind="system.probe",
            dedupe_key=f"probe:{idempotency_key}",
            payload={},
        )
        if created:
            db.add(
                AuditEvent(
                    tenant_id=principal.tenant_id,
                    actor_id=principal.actor_id,
                    event_type="job.enqueued",
                    entity_type="background_job",
                    entity_id=job.id,
                    reason="framework_probe",
                    request_id=request.state.request_id,
                )
            )
    return job


@router.get("/jobs/{job_id}", response_model=JobView, tags=["Jobs"])
async def get_job(job_id: UUID, principal: Admin, db: Session):
    job = await db.scalar(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.tenant_id == principal.tenant_id,
        )
    )
    if job is None:
        raise HTTPException(404, "job_not_found")
    return job


@router.post(
    "/retrieve",
    response_model=RetrieveResponse,
    tags=["Retrieval"],
    responses={501: {"description": "M3 retrieval pipeline is not implemented yet"}},
)
async def retrieve(body: RetrieveRequest, principal: Reader):
    raise HTTPException(501, "retrieval_not_implemented")
