"""Single-worker deployment heartbeat on the shared asset volume."""

import asyncio
import importlib.util
import json
import os
import time
from pathlib import Path

from rag_portfolio.adapters.ingestion import atomic_write, canonical
from rag_portfolio.services.documents import DocumentServiceError

SUPPORTED_KINDS = ("system.probe", "document.ingest", "release.build")


def heartbeat_path(settings):
    return Path(settings.asset_root) / "runtime" / "worker-heartbeat.json"


def capabilities():
    return {
        "task_kinds": list(SUPPORTED_KINDS),
        "pdf_ingestion": importlib.util.find_spec("docling") is not None,
    }


def processing_status(settings):
    try:
        row = json.loads(heartbeat_path(settings).read_text(encoding="utf-8"))
        ready = (
            0 <= time.time() - row["timestamp"] <= settings.worker_heartbeat_max_age
            and row["build_revision"] == settings.build_revision
            and set(SUPPORTED_KINDS).issubset(row["task_kinds"])
            and row.get("pdf_ingestion") is True
        )
        return {
            "status": "ready" if ready else "not_ready",
            "worker": row,
            "expected_build_revision": settings.build_revision,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "status": "not_ready",
            "worker": None,
            "expected_build_revision": settings.build_revision,
        }


def require_processing_ready(settings):
    if processing_status(settings)["status"] != "ready":
        raise DocumentServiceError("processing_worker_not_ready", 503)


async def heartbeat_loop(settings):
    path = heartbeat_path(settings)
    while True:
        row = {
            "timestamp": time.time(),
            "pid": os.getpid(),
            "build_revision": settings.build_revision,
            **capabilities(),
        }
        await asyncio.to_thread(atomic_write, path, canonical(row))
        await asyncio.sleep(max(1, settings.worker_heartbeat_max_age / 3))
