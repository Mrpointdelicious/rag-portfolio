"""Freeze the exact four manually approved imported versions plus hospital revision 2."""

import argparse
import asyncio
import sys
from pathlib import Path

from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.evaluation.corpus import (
    DEFAULT_CORPUS_PATH,
    EvaluationError,
    read_snapshot,
    snapshot_from_db,
    versions_from_import_report,
)
from rag_portfolio.evaluation.retrieval import atomic_write
from rag_portfolio.event_loop import loop_factory


async def freeze(settings: Settings, import_report: Path, output: Path) -> None:
    versions = versions_from_import_report(import_report)
    previous = read_snapshot(output) if output.exists() else None
    database = Database(settings)
    try:
        async with database.sessions() as session:
            snapshot, _ = await snapshot_from_db(
                session,
                settings.tenant_id,
                versions,
                created_at=previous.created_at if previous else None,
            )
        if previous is not None and snapshot != previous:
            raise EvaluationError("Existing snapshot differs; refuse to overwrite a frozen corpus")
        if previous is None:
            atomic_write(output, snapshot.model_dump_json(indent=2) + "\n")
        print(f"Corpus: {snapshot.corpus_id}")
        print(f"Documents: {len(snapshot.documents)}")
        print(f"Total chunks: {snapshot.chunk_count}")
        for doc in snapshot.documents:
            print(
                f"{doc.external_key}: version={doc.version_id} "
                f"revision={doc.revision} chunks={len(doc.chunks)}"
            )
        print(f"Snapshot: {output} ({'unchanged' if previous else 'created'})")
    finally:
        await database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--import-report",
        type=Path,
        default=Path("data/eval_sources/yueyang/import_report.json"),
        help="Importer report with the exact four version UUIDs; default: %(default)s",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CORPUS_PATH,
        help="Local frozen snapshot; default: %(default)s",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(freeze(Settings(), args.import_report, args.output), loop_factory=loop_factory)
    except EvaluationError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}; check local corpus and PostgreSQL configuration")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
