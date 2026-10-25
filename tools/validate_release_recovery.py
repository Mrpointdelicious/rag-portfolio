"""Exercise release recovery on the configured rehab_kb; creates real release/job rows.

Run only while other workers and publication operators are stopped. No database,
tenant, corpus, or document is created. Candidate indexes/audits are retained.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import func, select

from rag_portfolio.adapters.vector import QdrantConnection
from rag_portfolio.config import Settings
from rag_portfolio.db.models import BackgroundJob, KnowledgeBase, KnowledgeRelease, ReleaseDocument
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.services.documents import ActorContext, DocumentServiceError
from rag_portfolio.services.jobs import ClaimedJob, JobQueue
from rag_portfolio.services.releases import activate_release, create_release, verify_index
from rag_portfolio.worker import run_once


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class TargetQueue(JobQueue):
    """Use normal worker completion/renewal but never claim unrelated jobs."""

    def __init__(self, database, settings, target):
        super().__init__(database.sessions, settings.tenant_id, settings.worker_lease_seconds, 1)
        self.target = target

    async def claim(self):
        async with self.sessions.begin() as session:
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == self.target,
                    BackgroundJob.tenant_id == self.tenant_id,
                    BackgroundJob.status == "queued",
                    BackgroundJob.next_run_at <= func.clock_timestamp(),
                )
                .with_for_update()
            )
            require(job is not None, "Validation job is not queued; stop concurrent workers")
            token = uuid4()
            job.status, job.lease_token = "running", token
            job.attempts += 1
            job.lease_until = func.clock_timestamp() + timedelta(seconds=self.lease_seconds)
            return ClaimedJob(
                job.id, job.tenant_id, job.kind, job.payload_json, job.attempts, token
            )


async def validate(settings, kb_id=None):
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    database, vector = Database(settings), QdrantConnection(settings)
    actor = ActorContext(settings.tenant_id, "release-recovery-validation", uuid4())
    original_id = candidate_id = selected_kb = None
    report = {"checks": [], "original_release_id": None, "candidate_release_id": None}
    try:
        async with database.sessions() as session:
            query = select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == settings.tenant_id,
                KnowledgeBase.code == "rehab_kb",
                KnowledgeBase.enabled.is_(True),
                KnowledgeBase.active_release_id.is_not(None),
            )
            if kb_id:
                query = query.where(KnowledgeBase.id == kb_id)
            bases = list(await session.scalars(query))
            require(len(bases) == 1, "Need exactly one active rehab_kb; use --kb-id if ambiguous")
            selected_kb, original_id = bases[0].id, bases[0].active_release_id
            original = await session.get(KnowledgeRelease, original_id)
            require(original.status == "active", "Original publication is not active")
            await verify_index(vector.client, original)
            version_ids = list(
                await session.scalars(
                    select(ReleaseDocument.version_id).where(
                        ReleaseDocument.release_id == original_id
                    )
                )
            )
        async with database.sessions.begin() as session:
            candidate, job = await create_release(
                session, settings, actor, selected_kb, version_ids
            )
            candidate_id, job_id = candidate.id, job.id
        report.update(
            original_release_id=str(original_id),
            candidate_release_id=str(candidate_id),
            job_id=str(job_id),
            collection=candidate.collection_name,
        )
        print(json.dumps({"created": report}, ensure_ascii=False), flush=True)
        queue = TargetQueue(database, settings, job_id)
        real_add, injected = QdrantVectorStore.async_add, False

        async def interrupt_after_write(store, nodes, *args, **kwargs):
            nonlocal injected
            result = await real_add(store, nodes, *args, **kwargs)
            if store.collection_name == candidate.collection_name and not injected:
                injected = True
                raise DocumentServiceError("validation_interrupted_after_real_qdrant_write")
            return result

        with patch.object(QdrantVectorStore, "async_add", interrupt_after_write):
            await run_once(queue, database=database, settings=settings)
        require(injected, "Fault did not execute after a real write")
        count = (await vector.client.count(candidate.collection_name, exact=True)).count
        require(count > 0, "Interrupted candidate has no real Qdrant points")
        async with database.sessions() as session:
            kb = await session.get(KnowledgeBase, selected_kb)
            failed_job = await session.get(BackgroundJob, job_id)
            failed_release = await session.get(KnowledgeRelease, candidate_id)
            require(kb.active_release_id == original_id, "Failure changed active release")
            require(failed_job.status == "failed", "Worker did not mark job failed")
            require(failed_release.status == "failed", "Worker did not mark release failed")
        report["checks"].append("real_partial_write_failed_without_switching_active")
        # Same transition as the production retry endpoint; no synthetic worker result.
        async with database.sessions.begin() as session:
            job = await session.get(BackgroundJob, job_id, with_for_update=True)
            require(job.status == "failed", "Job state changed before retry")
            job.status, job.attempts, job.finished_at = "queued", 0, None
            job.next_run_at, job.last_error_code = datetime.now(UTC), None
        await run_once(queue, database=database, settings=settings)
        async with database.sessions() as session:
            candidate = await session.get(KnowledgeRelease, candidate_id)
            job = await session.get(BackgroundJob, job_id)
            require(job.status == "succeeded" and candidate.status == "ready", "Retry not ready")
            await verify_index(vector.client, candidate)
            count = (await vector.client.count(candidate.collection_name, exact=True)).count
            require(
                count == len(candidate.manifest_json["chunks"]), "Recovered point count differs"
            )
        report["checks"].append("formal_worker_retry_ready_with_exact_manifest")
        async with database.sessions.begin() as session:
            kb = await session.get(KnowledgeBase, selected_kb, with_for_update=True)
            require(kb.active_release_id == original_id, "Concurrent publisher detected")
            await activate_release(session, actor, candidate_id, "recovery_validation_activate")
        async with database.sessions() as session:
            require(
                (await session.get(KnowledgeBase, selected_kb)).active_release_id == candidate_id,
                "Activation did not update current pointer",
            )
        report["checks"].append("candidate_activated")
        async with database.sessions.begin() as session:
            original = await session.get(KnowledgeRelease, original_id)
            await verify_index(vector.client, original)
            await activate_release(
                session, actor, original_id, "recovery_validation_rollback", rollback=True
            )
        report["checks"].append("original_rolled_back")
        report["point_count"] = count
    finally:
        try:
            if selected_kb and candidate_id:
                async with database.sessions.begin() as session:
                    kb = await session.get(KnowledgeBase, selected_kb, with_for_update=True)
                    if kb.active_release_id == candidate_id:
                        await activate_release(
                            session,
                            actor,
                            original_id,
                            "recovery_validation_finally_restore",
                            rollback=True,
                        )
                    require(
                        kb.active_release_id == original_id,
                        "Original active not restored; concurrent publication requires review",
                    )
                report["final_active_release_id"] = str(original_id)
        finally:
            await vector.close()
            await database.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    output = Path(settings.asset_root) / "validation" / "release-recovery.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-id", type=UUID)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Acknowledge real candidate writes and brief activate/rollback",
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error("Pass --execute with normal workers/publication operators stopped")
    asyncio.run(validate(Settings(), args.kb_id), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
