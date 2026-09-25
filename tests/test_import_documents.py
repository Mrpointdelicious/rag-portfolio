"""Unit and integration tests for the document import tool (tools/import_documents.py)."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from rag_portfolio.db.models import DocumentVersion, KnowledgeBase, SourceDocument
from rag_portfolio.importing import (
    DocumentConfig,
    ImportRecord,
    ImportReport,
    Manifest,
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

KB_ID = "00a1404f-956c-47ad-936e-a106462c7e63"
EFFECTIVE = "2026-09-25T00:00:00+08:00"
TZ_0800 = timezone(timedelta(hours=8))

BASE_DOC = {
    "external_key": "hospital-overview",
    "title": "岳阳医院场景总览",
    "filename": "hospital_overview.md",
    "source_locator": "yueyang/scene/hospital-overview.md",
}

DEFAULTS = {
    "audiences": ["patient", "doctor"],
    "effective_at": EFFECTIVE,
    "expires_at": None,
    "metadata": {"hospital_id": "yueyang_hospital_demo", "topic": "scene_intro"},
}


def write_manifest(tmp_path: Path, *, documents: list[dict] | None = None, **overrides) -> Path:
    payload = {
        "kb_id": KB_ID,
        "tenant_id": "portfolio",
        "source_instance_id": "yueyang-scene-docs",
        "defaults": DEFAULTS,
        "documents": documents if documents is not None else [dict(BASE_DOC)],
    }
    payload.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def write_document(
    tmp_path: Path,
    filename: str = "hospital_overview.md",
    content: str | bytes = "# Demo\n\n演示内容。\n",
) -> Path:
    (tmp_path / "documents").mkdir(exist_ok=True)
    path = tmp_path / "documents" / filename
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def config_for(manifest: Manifest, index: int = 0) -> DocumentConfig:
    entry = manifest.documents[index]
    config, errors = build_document_config(entry, merge_document_config(manifest.defaults, entry))
    assert not errors and config is not None
    return config


def codes(errors: list[tuple[str, str]]) -> list[str]:
    return [code for code, _ in errors]


def build_manifest(kb_id: UUID, tenant_id: str, documents: list[dict] | None = None) -> Manifest:
    return Manifest(
        kb_id=kb_id,
        tenant_id=tenant_id,
        source_instance_id="yueyang-scene-docs",
        defaults=dict(DEFAULTS),
        documents=documents if documents is not None else [dict(BASE_DOC)],
        path=Path.cwd(),
    )


async def make_kb(session, tenant_id: str) -> KnowledgeBase:
    kb = KnowledgeBase(
        tenant_id=tenant_id,
        scope_key="shared",
        code="hospital_kb",
        hospital_id="yueyang_hospital_demo",
    )
    session.add(kb)
    await session.flush()
    return kb


async def count(session, model, kb_id: UUID) -> int:
    value = await session.scalar(
        select(func.count()).select_from(model).where(model.kb_id == kb_id)
    )
    return value or 0


# --- Manifest / file validation (no database) ---


def test_markdown_manifest_and_file_pass(tmp_path):
    manifest_path = write_manifest(tmp_path)
    write_document(tmp_path, content="# Demo\n\n演示内容。\n")
    manifest = load_manifest(manifest_path)
    assert str(manifest.kb_id) == KB_ID and manifest.tenant_id == "portfolio"
    config = config_for(manifest)
    check = check_document_file(manifest, config.filename)
    assert check.ok and check.content == "# Demo\n\n演示内容。\n".encode()


def test_txt_document_passes(tmp_path):
    document = {**BASE_DOC, "external_key": "notes", "filename": "notes.txt"}
    manifest_path = write_manifest(tmp_path, documents=[document])
    write_document(tmp_path, "notes.txt", "纯文本演示内容。\n")
    manifest = load_manifest(manifest_path)
    config = config_for(manifest)
    check = check_document_file(manifest, config.filename)
    assert check.ok


def test_duplicate_external_key_rejected(tmp_path):
    manifest_path = write_manifest(tmp_path, documents=[dict(BASE_DOC), dict(BASE_DOC)])
    with pytest.raises(ManifestError) as info:
        load_manifest(manifest_path)
    assert "duplicate_external_key" in codes(info.value.errors)


def test_unsupported_extension_rejected(tmp_path):
    document = {**BASE_DOC, "filename": "hospital_overview.pdf"}
    manifest_path = write_manifest(tmp_path, documents=[document])
    write_document(tmp_path, "hospital_overview.pdf", "%PDF-1.4 fake\n")
    manifest = load_manifest(manifest_path)
    check = check_document_file(manifest, config_for(manifest).filename)
    assert not check.ok and check.error_code == "unsupported_extension"


def test_missing_file_rejected(tmp_path):
    manifest = load_manifest(write_manifest(tmp_path))
    check = check_document_file(manifest, config_for(manifest).filename)
    assert not check.ok and check.error_code == "file_not_found"


def test_path_traversal_rejected(tmp_path):
    document = {**BASE_DOC, "filename": "../secret.md"}
    manifest_path = write_manifest(tmp_path, documents=[document])
    (tmp_path / "secret.md").write_text("# Secret\n\n不该被读取。\n", encoding="utf-8")
    manifest = load_manifest(manifest_path)
    check = check_document_file(manifest, config_for(manifest).filename)
    assert not check.ok and check.error_code == "path_traversal"


def test_naive_effective_at_rejected(tmp_path):
    naive_defaults = {**DEFAULTS, "effective_at": "2026-09-25T00:00:00"}
    manifest = load_manifest(write_manifest(tmp_path, defaults=naive_defaults))
    config, errors = build_document_config(
        manifest.documents[0], merge_document_config(manifest.defaults, manifest.documents[0])
    )
    assert config is None and "timezone_required" in codes(errors)


def test_expires_at_not_after_effective_at_rejected(tmp_path):
    defaults = {**DEFAULTS, "expires_at": "2026-09-25T00:00:00+08:00"}
    manifest = load_manifest(write_manifest(tmp_path, defaults=defaults))
    config, errors = build_document_config(
        manifest.documents[0], merge_document_config(manifest.defaults, manifest.documents[0])
    )
    assert config is None and "invalid_effective_window" in codes(errors)


def test_invalid_audience_rejected(tmp_path):
    manifest = load_manifest(
        write_manifest(tmp_path, defaults={**DEFAULTS, "audiences": ["patient", "guest"]})
    )
    config, errors = build_document_config(
        manifest.documents[0], merge_document_config(manifest.defaults, manifest.documents[0])
    )
    assert config is None and "invalid_audience" in codes(errors)


def test_invalid_kb_id_rejected(tmp_path):
    manifest_path = write_manifest(tmp_path, kb_id="not-a-uuid")
    with pytest.raises(ManifestError) as info:
        load_manifest(manifest_path)
    assert "kb_id_invalid" in codes(info.value.errors)


def test_empty_documents_rejected(tmp_path):
    manifest_path = write_manifest(tmp_path, documents=[])
    with pytest.raises(ManifestError) as info:
        load_manifest(manifest_path)
    assert "documents_required" in codes(info.value.errors)


def test_document_overrides_defaults_and_merges_metadata():
    document = {
        **BASE_DOC,
        "external_key": "rehab-area",
        "title": "康复区域",
        "filename": "rehab_area.md",
        "source_locator": "yueyang/scene/rehab-area.md",
        "audiences": ["internal"],
        "metadata": {"scene_name": "康复区域"},
    }
    merged = merge_document_config(DEFAULTS, document)
    assert merged["audiences"] == ["internal"]
    assert merged["expires_at"] is None
    assert merged["metadata"]["topic"] == "scene_intro"
    assert merged["metadata"]["scene_name"] == "康复区域"
    config, errors = build_document_config(document, merged)
    assert not errors and config is not None and config.audiences == ["internal"]


def test_document_null_expires_at_clears_default():
    defaults = {**DEFAULTS, "expires_at": "2027-01-01T00:00:00+08:00"}
    merged = merge_document_config(defaults, {**BASE_DOC, "expires_at": None})
    assert merged["expires_at"] is None


def test_write_report_roundtrip(tmp_path):
    record = ImportRecord(
        external_key="hospital-overview",
        filename="hospital_overview.md",
        status="success",
        document_created=True,
        document_id=uuid4(),
        version_created=True,
        version_id=uuid4(),
        revision=1,
        review_status="pending",
        content_hash="a" * 64,
        metadata_hash="b" * 64,
    )
    report = ImportReport(
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        kb_id=uuid4(),
        tenant_id="portfolio",
        source_instance_id="yueyang-scene-docs",
        records=[record],
    )
    path = tmp_path / "import_report.json"
    write_report(path, report)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["summary"] == {
        "total": 1,
        "succeeded": 1,
        "failed": 0,
        "documents_created": 1,
        "documents_reused": 0,
        "versions_created": 1,
        "versions_reused": 0,
    }
    assert payload["documents"][0]["review_status"] == "pending"
    assert payload["documents"][0]["content_hash"] == "a" * 64


# --- Database integration ---


@pytest.mark.integration
async def test_first_import_creates_document_and_pending_version(database, tenant):
    async with database.sessions.begin() as session:
        kb = await make_kb(session, tenant)
    manifest = build_manifest(kb.id, tenant)
    config = config_for(manifest)
    content = "# Demo\n\n这是演示内容。\n".encode()
    async with database.sessions.begin() as session:
        record = await import_one_document(session, manifest, config, content)
    assert record.status == "success"
    assert record.document_created and record.version_created
    assert record.revision == 1 and record.review_status == "pending"
    async with database.sessions() as session:
        assert await count(session, SourceDocument, kb.id) == 1
        versions = (
            await session.scalars(select(DocumentVersion).where(DocumentVersion.kb_id == kb.id))
        ).all()
        assert len(versions) == 1
        assert versions[0].revision == 1 and versions[0].review_status == "pending"


@pytest.mark.integration
async def test_identical_reimport_reuses_document_and_version(database, tenant):
    async with database.sessions.begin() as session:
        kb = await make_kb(session, tenant)
    manifest = build_manifest(kb.id, tenant)
    config = config_for(manifest)
    content = "# Demo\n\n这是演示内容。\n".encode()
    async with database.sessions.begin() as session:
        first = await import_one_document(session, manifest, config, content)
    async with database.sessions.begin() as session:
        second = await import_one_document(session, manifest, config, content)
    assert first.document_created and first.version_created
    assert not second.document_created and not second.version_created
    assert second.revision == 1 and second.review_status == "pending"
    async with database.sessions() as session:
        assert await count(session, SourceDocument, kb.id) == 1
        assert await count(session, DocumentVersion, kb.id) == 1


@pytest.mark.integration
async def test_content_change_creates_new_revision(database, tenant):
    async with database.sessions.begin() as session:
        kb = await make_kb(session, tenant)
    manifest = build_manifest(kb.id, tenant)
    config = config_for(manifest)
    async with database.sessions.begin() as session:
        first = await import_one_document(
            session, manifest, config, "# Demo\n\n第一版内容。\n".encode()
        )
    async with database.sessions.begin() as session:
        second = await import_one_document(
            session, manifest, config, "# Demo\n\n第二版修改内容。\n".encode()
        )
    assert first.version_created and second.version_created
    assert not second.document_created and second.version_id != first.version_id
    assert second.revision == 2 and second.review_status == "pending"
    async with database.sessions() as session:
        assert await count(session, SourceDocument, kb.id) == 1
        versions = (
            await session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.kb_id == kb.id)
                .order_by(DocumentVersion.revision.asc())
            )
        ).all()
        assert [version.revision for version in versions] == [1, 2]
        assert versions[-1].review_status == "pending"


@pytest.mark.integration
async def test_one_failure_does_not_rollback_others(database, tenant):
    async with database.sessions.begin() as session:
        kb = await make_kb(session, tenant)
    manifest = build_manifest(
        kb.id,
        tenant,
        documents=[
            dict(BASE_DOC),
            {
                "external_key": "broken-doc",
                "title": "B 文档",
                "filename": "broken.md",
                "source_locator": "yueyang/scene/broken.md",
            },
        ],
    )
    config = config_for(manifest, 0)
    content = "# Demo\n\n演示内容。\n".encode()
    async with database.sessions.begin() as session:
        record = await import_one_document(session, manifest, config, content)
    assert record.status == "success"
    broken = DocumentConfig(
        external_key="broken-doc",
        title="B 文档",
        filename="broken.md",
        source_locator="yueyang/scene/broken.md",
        audiences=["patient"],
        effective_at=datetime(2026, 9, 25, tzinfo=TZ_0800),
        expires_at=None,
        metadata={"unserializable": {1, 2}},
    )
    with pytest.raises(DocumentServiceError) as info:
        async with database.sessions.begin() as session:
            await import_one_document(session, manifest, broken, content)
    assert info.value.code == "invalid_metadata"
    async with database.sessions() as session:
        documents = (
            await session.scalars(select(SourceDocument).where(SourceDocument.kb_id == kb.id))
        ).all()
        assert [document.external_key for document in documents] == ["hospital-overview"]
        assert await count(session, DocumentVersion, kb.id) == 1


@pytest.mark.integration
async def test_validate_checks_are_read_only(database, tenant):
    async with database.sessions.begin() as session:
        kb = await make_kb(session, tenant)
    manifest = build_manifest(kb.id, tenant)
    config_for(manifest)
    async with database.sessions() as session:
        assert await check_knowledge_base(session, manifest) == []
        before = (
            await count(session, SourceDocument, kb.id),
            await count(session, DocumentVersion, kb.id),
        )
    mismatched = build_manifest(kb.id, "other_" + tenant)
    async with database.sessions() as session:
        errors = await check_knowledge_base(session, mismatched)
        after = (
            await count(session, SourceDocument, kb.id),
            await count(session, DocumentVersion, kb.id),
        )
    assert errors and errors[0][0] == "knowledge_base_tenant_mismatch"
    assert after == before


@pytest.mark.integration
async def test_collect_status_reports_imported_and_missing(database, tenant):
    async with database.sessions.begin() as session:
        kb = await make_kb(session, tenant)
    manifest = build_manifest(
        kb.id,
        tenant,
        documents=[
            dict(BASE_DOC),
            {
                "external_key": "not-imported",
                "title": "未导入文档",
                "filename": "not_imported.md",
                "source_locator": "yueyang/scene/not-imported.md",
            },
        ],
    )
    async with database.sessions.begin() as session:
        await import_one_document(
            session, manifest, config_for(manifest, 0), "# Demo\n\n演示内容。\n".encode()
        )
    async with database.sessions() as session:
        rows = await collect_status(session, manifest)
    assert rows[0].document == "found"
    assert rows[0].revision == 1 and rows[0].review_status == "pending"
    assert rows[1].document == "not imported"
    assert rows[1].revision is None and rows[1].review_status is None
