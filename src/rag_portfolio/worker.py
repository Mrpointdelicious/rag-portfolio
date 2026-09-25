import argparse
import asyncio
import logging
import signal
from contextlib import suppress

from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.services.jobs import JobQueue

logger = logging.getLogger(__name__)


async def run_once(queue: JobQueue) -> bool:
    job = await queue.claim()
    if job is None:
        return False
    if job.kind != "system.probe":
        await queue.fail(job, "handler_not_implemented", permanent=True)
        return True
    committed = await queue.complete(job, {"probe": "ok"})
    logger.info("job=%s kind=%s committed=%s", job.id, job.kind, committed)
    return True


async def run(settings: Settings, *, once: bool = False) -> None:
    database = Database(settings)
    queue = JobQueue(
        database.sessions,
        settings.tenant_id,
        settings.worker_lease_seconds,
        settings.worker_max_attempts,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        while not stop.is_set():
            try:
                worked = await run_once(queue)
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
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(run(Settings(), once=args.once), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
