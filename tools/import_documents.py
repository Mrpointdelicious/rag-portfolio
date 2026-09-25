"""Import external Markdown/TXT documents into the pending review queue.

validate  checks the manifest, files and knowledge base without writing anything.
import    runs the validate preflight, then imports each document in its own transaction.
status    shows the current document/version state for the manifest entries.
"""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.importing import (
    ImportRecord,
    ImportReport,
    ManifestError,
    build_document_config,
    check_document_file,
    check_knowledge_base,
    collect_status,
    import_one_document,
    load_manifest,
    merge_document_config,
    write_report,
)
from rag_portfolio.services.documents import DocumentServiceError


def _print_manifest_errors(exc: ManifestError) -> None:
    print("Manifest: FAIL")
    for code, message in exc.errors:
        print(f"  {code}: {message}")


async def run_validate(settings: Settings, manifest_path: Path) -> int:
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        _print_manifest_errors(exc)
        return 1
    print("Manifest: OK")
    database = Database(settings)
    failed = 0
    try:
        try:
            async with database.sessions() as session:
                kb_errors = await check_knowledge_base(session, manifest)
        except Exception as exc:
            print(f"Knowledge Base: FAIL (unreachable: {type(exc).__name__}: {exc})")
            failed += 1
            kb_errors = []
        else:
            if kb_errors:
                print("Knowledge Base: FAIL")
                for code, message in kb_errors:
                    print(f"  {code}: {message}")
                failed += 1
            else:
                print("Knowledge Base: OK")
        print()
        checked = 0
        for entry in manifest.documents:
            display = str(entry.get("filename") or entry.get("external_key") or "?")
            config, config_errors = build_document_config(
                entry, merge_document_config(manifest.defaults, entry)
            )
            if config_errors:
                for code, message in config_errors:
                    print(f"[FAIL] {display}: {code}: {message}")
                    failed += 1
                continue
            check = check_document_file(manifest, config.filename)
            checked += 1
            if check.ok:
                print(f"[OK] {config.filename}")
            else:
                print(f"[FAIL] {config.filename}: {check.error_code}: {check.message}")
                failed += 1
        print()
        print(f"Validated: {checked}")
        print(f"Errors: {failed}")
    finally:
        await database.close()
    return 1 if failed else 0


def _failure_record(
    external_key: str, filename: str, errors: list[tuple[str, str]]
) -> ImportRecord:
    code, message = errors[0]
    return ImportRecord(
        external_key=external_key,
        filename=filename,
        status="failed",
        error_code=code,
        message=message,
    )


async def run_import(settings: Settings, manifest_path: Path) -> int:
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        _print_manifest_errors(exc)
        return 1
    database = Database(settings)
    started = datetime.now(UTC)
    records: list[ImportRecord] = []
    try:
        try:
            async with database.sessions() as session:
                kb_errors = await check_knowledge_base(session, manifest)
        except Exception as exc:
            print(f"Knowledge Base: FAIL (unreachable: {type(exc).__name__}: {exc})")
            return 1
        if kb_errors:
            print("Knowledge Base: FAIL")
            for code, message in kb_errors:
                print(f"  {code}: {message}")
            return 1
        print("RAG Portfolio Document Import")
        print()
        print("KB:")
        print(manifest.kb_id)
        print()
        print("Tenant:")
        print(manifest.tenant_id)
        print()
        print("Source:")
        print(manifest.source_instance_id)
        print()
        database_info = (
            f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_database}"
        )
        print(f"Database: {database_info}")
        print()
        for index, entry in enumerate(manifest.documents, start=1):
            display = str(entry.get("filename") or entry.get("external_key") or "?")
            print(f"[{index}/{len(manifest.documents)}] {display}")
            config, config_errors = build_document_config(
                entry, merge_document_config(manifest.defaults, entry)
            )
            if config_errors:
                for code, message in config_errors:
                    print(f"  {code}: {message}")
                records.append(
                    _failure_record(str(entry.get("external_key") or ""), display, config_errors)
                )
                print("  FAIL")
                print()
                continue
            check = check_document_file(manifest, config.filename)
            if not check.ok:
                records.append(
                    ImportRecord(
                        external_key=config.external_key,
                        filename=config.filename,
                        status="failed",
                        error_code=check.error_code,
                        message=check.message,
                    )
                )
                print(f"  {check.error_code}: {check.message}")
                print("  FAIL")
                print()
                continue
            assert check.content is not None
            try:
                async with database.sessions.begin() as session:
                    records.append(
                        await import_one_document(session, manifest, config, check.content)
                    )
            except DocumentServiceError as exc:
                records.append(
                    ImportRecord(
                        external_key=config.external_key,
                        filename=config.filename,
                        status="failed",
                        error_code=exc.code,
                        message=str(exc),
                    )
                )
            except Exception as exc:
                records.append(
                    ImportRecord(
                        external_key=config.external_key,
                        filename=config.filename,
                        status="failed",
                        error_code="internal_error",
                        message=f"{type(exc).__name__}: {exc}"[:300],
                    )
                )
            record = records[-1]
            if record.status == "success":
                print(f"  document: {'created' if record.document_created else 'reused'}")
                print(f"  document_id: {record.document_id}")
                print(
                    f"  version: {'created' if record.version_created else 'reused'} "
                    f"revision={record.revision}"
                )
                print(f"  review: {record.review_status}")
                print("  OK")
            else:
                print(f"  {record.error_code}: {record.message}")
                print("  FAIL")
            print()
        report = ImportReport(
            started_at=started,
            finished_at=datetime.now(UTC),
            kb_id=manifest.kb_id,
            tenant_id=manifest.tenant_id,
            source_instance_id=manifest.source_instance_id,
            records=records,
        )
        report_path = manifest_path.parent / "import_report.json"
        write_report(report_path, report)
        print(f"Report: {report_path}")
        print()
        print("Summary")
        print()
        for key, value in report.summary().items():
            print(f"{key}: {value}")
    finally:
        await database.close()
    return 0 if report.summary()["failed"] == 0 else 1


async def run_status(settings: Settings, manifest_path: Path) -> int:
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        _print_manifest_errors(exc)
        return 1
    database = Database(settings)
    try:
        try:
            async with database.sessions() as session:
                rows = await collect_status(session, manifest)
        except Exception as exc:
            print(f"Database: FAIL ({type(exc).__name__}: {exc})")
            return 1
        print(f"{'external_key':<26}{'document':<14}{'revision':<10}status")
        print("-" * 64)
        for row in rows:
            revision = "-" if row.revision is None else str(row.revision)
            status = "-" if row.review_status is None else row.review_status
            print(f"{row.external_key:<26}{row.document:<14}{revision:<10}{status}")
        return 0
    finally:
        await database.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import_documents",
        description=(
            "Validate and import external Markdown/TXT documents into the pending review queue."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "import", "status"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("manifest", type=Path, help="path to manifest.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest_path: Path = args.manifest
    if not manifest_path.is_file():
        print(f"Manifest file not found: {manifest_path}")
        return 1
    handlers = {"validate": run_validate, "import": run_import, "status": run_status}
    return asyncio.run(handlers[args.command](Settings(), manifest_path), loop_factory=loop_factory)


if __name__ == "__main__":
    raise SystemExit(main())
