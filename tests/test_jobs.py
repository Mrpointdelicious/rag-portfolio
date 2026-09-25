import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from rag_portfolio.db.models import BackgroundJob
from rag_portfolio.services.jobs import JobQueue, enqueue
from rag_portfolio.worker import run_once

pytestmark = pytest.mark.integration


async def submit(database, tenant, key=None, kind="system.probe"):
    async with database.sessions.begin() as session:
        return await enqueue(
            session, tenant_id=tenant, kind=kind, dedupe_key=key or uuid4().hex, payload={}
        )


async def expire(database, job):
    async with database.sessions.begin() as session:
        await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job.id)
            .values(lease_until=func.now() - timedelta(seconds=1))
        )


async def test_enqueue_is_idempotent_and_transactional(database, tenant):
    first, created = await submit(database, tenant, "repeat")
    second, created_again = await submit(database, tenant, "repeat")
    assert created and not created_again and first.id == second.id
    async with database.sessions() as session:
        await enqueue(
            session, tenant_id=tenant, kind="system.probe", dedupe_key="rollback", payload={}
        )
        await session.rollback()
    async with database.sessions() as session:
        absent = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.tenant_id == tenant, BackgroundJob.dedupe_key == "rollback"
            )
        )
        assert absent is None


async def test_concurrent_claims_are_distinct(database, tenant):
    await submit(database, tenant)
    await submit(database, tenant)
    queue = JobQueue(database.sessions, tenant)
    first, second = await asyncio.gather(queue.claim(), queue.claim())
    assert first and second and first.id != second.id
    assert await queue.claim() is None


async def test_expired_worker_cannot_commit_after_reclaim(database, tenant):
    await submit(database, tenant)
    queue = JobQueue(database.sessions, tenant)
    old = await queue.claim()
    await expire(database, old)
    assert not await queue.complete(old, {"stale": True})
    current = await queue.claim()
    assert current.id == old.id and current.lease_token != old.lease_token
    assert current.attempts == 2
    assert not await queue.fail(old, "late_failure")
    assert await queue.complete(current, {"probe": "ok"})


async def test_expired_final_attempt_becomes_terminal(database, tenant):
    await submit(database, tenant)
    queue = JobQueue(database.sessions, tenant, max_attempts=1)
    claimed = await queue.claim()
    await expire(database, claimed)
    assert await queue.claim() is None
    async with database.sessions() as session:
        row = await session.get(BackgroundJob, claimed.id)
        assert row.status == "failed" and row.last_error_code == "lease_exhausted"


async def test_worker_probe_completes_and_unknown_handler_fails(database, tenant):
    queue = JobQueue(database.sessions, tenant)
    probe, _ = await submit(database, tenant)
    unknown, _ = await submit(database, tenant, kind="not.implemented")
    assert await run_once(queue)
    assert await run_once(queue)
    async with database.sessions() as session:
        good = await session.get(BackgroundJob, probe.id)
        bad = await session.get(BackgroundJob, unknown.id)
        assert good.status == "succeeded" and good.result_json == {"probe": "ok"}
        assert bad.status == "failed" and bad.last_error_code == "handler_not_implemented"


async def test_worker_cannot_claim_another_tenant(database, tenant):
    await submit(database, tenant)
    assert await JobQueue(database.sessions, "different_" + tenant).claim() is None
