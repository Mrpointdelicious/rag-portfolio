from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_portfolio.db.models import AuditEvent, BackgroundJob, KnowledgeRelease


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    tenant_id: str
    kind: str
    payload: dict
    attempts: int
    lease_token: UUID


async def require_job_lease(session: AsyncSession, job: ClaimedJob):
    owned = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.id == job.id,
            BackgroundJob.tenant_id == job.tenant_id,
            BackgroundJob.status == "running",
            BackgroundJob.lease_token == job.lease_token,
            BackgroundJob.lease_until > func.clock_timestamp(),
        )
        .with_for_update()
    )
    if owned is None:
        raise RuntimeError("job_lease_lost")


async def mark_release_failed(session, tenant_id, kind, payload, job_id, code):
    if kind != "release.build":
        return
    rid = UUID(payload["release_id"])
    await session.execute(
        update(KnowledgeRelease)
        .where(
            KnowledgeRelease.id == rid,
            KnowledgeRelease.tenant_id == tenant_id,
            KnowledgeRelease.status == "building",
        )
        .values(status="failed")
    )
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_id="worker",
            request_id=job_id,
            event_type="release.build_failed",
            entity_type="kb_release",
            entity_id=rid,
            reason=code,
        )
    )


async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: str,
    kind: str,
    dedupe_key: str,
    payload: dict,
) -> tuple[BackgroundJob, bool]:
    inserted = await session.scalar(
        insert(BackgroundJob)
        .values(tenant_id=tenant_id, kind=kind, dedupe_key=dedupe_key, payload_json=payload)
        .on_conflict_do_nothing(index_elements=["tenant_id", "dedupe_key"])
        .returning(BackgroundJob.id)
    )
    job = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.tenant_id == tenant_id,
            BackgroundJob.dedupe_key == dedupe_key,
        )
    )
    if job is None:
        raise RuntimeError("Job insert did not produce a visible row")
    if job.kind != kind or job.payload_json != payload:
        raise ValueError("idempotency_conflict")
    return job, inserted is not None


class JobQueue:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        tenant_id: str,
        lease_seconds: int = 60,
        max_attempts: int = 3,
        supported_kinds: tuple[str, ...] | None = None,
    ):
        self.sessions = sessions
        self.tenant_id = tenant_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.supported_kinds = supported_kinds

    async def claim(self) -> ClaimedJob | None:
        async with self.sessions.begin() as session:
            # An expired final attempt must become terminal, not remain running forever.
            expired = await session.execute(
                update(BackgroundJob)
                .where(
                    BackgroundJob.tenant_id == self.tenant_id,
                    BackgroundJob.status == "running",
                    BackgroundJob.lease_until <= func.now(),
                    BackgroundJob.attempts >= self.max_attempts,
                )
                .values(
                    status="failed",
                    lease_until=None,
                    lease_token=None,
                    finished_at=func.now(),
                    last_error_code="lease_exhausted",
                )
                .returning(BackgroundJob.id, BackgroundJob.kind, BackgroundJob.payload_json)
            )
            for expired_id, kind, payload in expired:
                await mark_release_failed(
                    session, self.tenant_id, kind, payload, expired_id, "lease_exhausted"
                )
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.tenant_id == self.tenant_id,
                    BackgroundJob.attempts < self.max_attempts,
                    BackgroundJob.kind.in_(self.supported_kinds)
                    if self.supported_kinds is not None
                    else True,
                    or_(
                        and_(
                            BackgroundJob.status == "queued",
                            BackgroundJob.next_run_at <= func.now(),
                        ),
                        and_(
                            BackgroundJob.status == "running",
                            BackgroundJob.lease_until <= func.now(),
                        ),
                    ),
                )
                .order_by(BackgroundJob.next_run_at, BackgroundJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            token = uuid4()
            job.status = "running"
            job.attempts += 1
            job.lease_token = token
            job.lease_until = func.now() + timedelta(seconds=self.lease_seconds)
            return ClaimedJob(
                job.id, job.tenant_id, job.kind, job.payload_json, job.attempts, token
            )

    def _owned(self, job: ClaimedJob):
        return and_(
            BackgroundJob.id == job.id,
            BackgroundJob.tenant_id == self.tenant_id,
            BackgroundJob.status == "running",
            BackgroundJob.lease_token == job.lease_token,
            BackgroundJob.lease_until > func.clock_timestamp(),
        )

    async def complete(self, job: ClaimedJob, result: dict) -> bool:
        async with self.sessions.begin() as session:
            response = await session.execute(
                update(BackgroundJob)
                .where(self._owned(job))
                .values(
                    status="succeeded",
                    result_json=result,
                    lease_token=None,
                    lease_until=None,
                    finished_at=func.now(),
                    last_error_code=None,
                )
            )
            return response.rowcount == 1

    async def renew(self, job: ClaimedJob) -> bool:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(BackgroundJob)
                .where(self._owned(job))
                .values(lease_until=func.clock_timestamp() + timedelta(seconds=self.lease_seconds))
            )
            return result.rowcount == 1

    async def progress(self, job: ClaimedJob, stage: str, **details) -> None:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(BackgroundJob)
                .where(self._owned(job))
                .values(result_json={"stage": stage, **details})
            )
            if result.rowcount != 1:
                raise RuntimeError("job_lease_lost")

    async def fail(self, job: ClaimedJob, code: str, *, permanent: bool = False) -> bool:
        terminal = permanent or job.attempts >= self.max_attempts
        async with self.sessions.begin() as session:
            response = await session.execute(
                update(BackgroundJob)
                .where(self._owned(job))
                .values(
                    status="failed" if terminal else "queued",
                    lease_token=None,
                    lease_until=None,
                    last_error_code=code,
                    finished_at=func.now() if terminal else None,
                    next_run_at=func.now() + timedelta(seconds=min(2**job.attempts, 60)),
                )
            )
            if response.rowcount == 1:
                await mark_release_failed(
                    session, self.tenant_id, job.kind, job.payload, job.id, code
                )
            return response.rowcount == 1
