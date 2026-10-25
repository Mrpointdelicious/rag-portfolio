import argparse
import asyncio
import logging
import signal
from contextlib import suppress

from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.services.jobs import JobQueue
from rag_portfolio.services.worker_status import SUPPORTED_KINDS, heartbeat_loop

logger = logging.getLogger(__name__)


async def run_once(queue: JobQueue, *, database=None, settings=None) -> bool:
    job = await queue.claim()
    if job is None:
        return False
    if job.kind not in {"system.probe", "document.ingest", "release.build"}:
        await queue.fail(job, "handler_not_implemented", permanent=True)
        return True
    if job.kind == "system.probe":
        result = {"probe": "ok"}
    else:
        from rag_portfolio.services.documents import DocumentServiceError
        from rag_portfolio.services.ingestion import ingest_document
        from rag_portfolio.services.releases import build_release

        async def heartbeat():
            while True:
                await asyncio.sleep(max(1, queue.lease_seconds / 3))
                if not await queue.renew(job):
                    raise RuntimeError("job_lease_lost")

        async def progress(stage, **details):
            await queue.progress(job, stage, **details)

        task = asyncio.create_task(
            (ingest_document if job.kind == "document.ingest" else build_release)(
                database, settings, job, progress=progress
            )
        )
        lease = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait({task, lease}, return_when=asyncio.FIRST_COMPLETED)
            if lease in done:
                await lease
            result = await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, DocumentServiceError) else type(exc).__name__
            await queue.fail(job, code, permanent=isinstance(exc, DocumentServiceError))
            logger.warning("job=%s failed code=%s", job.id, code)
            return True
        finally:
            task.cancel()
            lease.cancel()
            await asyncio.gather(task, lease, return_exceptions=True)
    committed = await queue.complete(job, result)
    logger.info("job=%s kind=%s committed=%s", job.id, job.kind, committed)
    return True


async def run(settings: Settings, *, once: bool = False) -> None:
    database = Database(settings)
    queue = JobQueue(
        database.sessions,
        settings.tenant_id,
        settings.worker_lease_seconds,
        settings.worker_max_attempts,
        supported_kinds=SUPPORTED_KINDS,
    )
    stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat_loop(settings))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        while not stop.is_set():
            try:
                worked = await run_once(queue, database=database, settings=settings)
            except Exception as exc:
                logger.error("worker_iteration_failed type=%s", type(exc).__name__)
                if once:
                    raise SystemExit(1) from None
                worked = False
            if once:
                break
            if not worked:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(run(Settings(), once=args.once), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
