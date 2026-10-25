import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from rag_portfolio.api.auth import Principal
from rag_portfolio.config import Settings
from rag_portfolio.contracts import KnowledgeBaseCreate, RetrieveRequest
from rag_portfolio.db.models import DocumentChunk, KnowledgeBase, SceneSnapshot
from rag_portfolio.services.documents import (
    ActorContext,
    DocumentService,
    DocumentServiceError,
    VersionInput,
)
from rag_portfolio.services.releases import activate_release, create_release
from rag_portfolio.services.relevance import admit, coverage_order, policy
from rag_portfolio.services.retrieval_algorithms import Candidate
from rag_portfolio.services.reviews import ReviewService
from rag_portfolio.services.scene_catalog import (
    approve,
    catalog_hash,
    derive_scenes,
    propose,
    published_catalogs,
)
from rag_portfolio.services.worker_status import SUPPORTED_KINDS, heartbeat_path, processing_status


def test_relevance_uses_the_actual_model_score_and_keeps_subquestion_coverage():
    a, b, c = uuid4(), uuid4(), uuid4()
    rules = policy("qwen3.7-text-rerank")
    ranked = [Candidate(a, 0.99), Candidate(b, 0.1), Candidate(c, 0.8)]
    assert [
        x.chunk_id
        for x in admit(ranked, {a: "hospital_kb", b: "hospital_kb", c: "rehab_kb"}, rules)
    ] == [a, c]
    assert [
        x.chunk_id for x in coverage_order([[ranked[0], ranked[1]], [ranked[2], ranked[0]]])
    ] == [a, c, b]
    with pytest.raises(DocumentServiceError, match="policy_model_mismatch"):
        policy("another-model")


def test_medical_knowledge_base_cannot_be_bound_to_a_space():
    from pydantic import ValidationError

    for code in ("rehab_kb", "clinical_kb"):
        with pytest.raises(ValidationError, match="must not bind"):
            KnowledgeBaseCreate(code=code, space_id=uuid4())


@pytest.mark.parametrize("mode", ["disabled", "outage", "low_score"])
async def test_unverified_or_low_relevance_candidates_never_fall_back_to_fusion(
    database, tenant, monkeypatch, mode
):
    from rag_portfolio.services import retrieval

    settings = Settings(_env_file=None)
    actor = ActorContext(tenant, "reviewer", uuid4())
    reader = Principal(tenant, "reader", False, ("shared",), "patient")
    async with database.sessions.begin() as session:
        kb = KnowledgeBase(tenant_id=tenant, code="hospital_kb", scope_key="shared")
        session.add(kb)
        await session.flush()
        documents = DocumentService(session, actor)
        source, _ = await documents.create_document(
            kb.id, title="医院测试指南", source_instance_id="test", external_key="guide"
        )
        version, _ = await documents.create_version(
            source.id,
            VersionInput(
                filename="guide.md",
                content="医院二楼有物理治疗诊疗室。",
                source_locator="test/guide",
                audiences=["patient"],
                effective_at=datetime.now(UTC) - timedelta(days=1),
            ),
        )
        await documents.submit_for_review(version.id, expected_row_version=version.row_version)
        await ReviewService(session, actor).approve_version(
            version.id,
            expected_row_version=version.row_version,
            expected_content_hash=version.content_hash,
            expected_metadata_hash=version.metadata_hash,
            reason="synthetic fixture",
        )
        release, _ = await create_release(session, settings, actor, kb.id, [version.id])
        release.status = "ready"
        await activate_release(session, actor, release.id, "synthetic fixture")
        chunk = await session.scalar(
            select(DocumentChunk).where(DocumentChunk.version_id == version.id)
        )
        node = SimpleNamespace(
            node_id=str(chunk.id),
            metadata={
                "content_hash": chunk.content_hash,
                "version_id": str(version.id),
            },
        )
        cid, kb_id = chunk.id, kb.id
    monkeypatch.setattr(
        retrieval,
        "query_candidates",
        AsyncMock(return_value=SimpleNamespace(nodes=[node], similarities=[100.0])),
    )
    rerank = AsyncMock(return_value=([Candidate(cid, 0.01)], {}))
    if mode == "outage":
        rerank.side_effect = ValueError("provider unavailable")
    monkeypatch.setattr(
        retrieval, "Reranker", lambda *args: SimpleNamespace(rerank=rerank, close=AsyncMock())
    )
    result = await retrieval.retrieve(
        database,
        SimpleNamespace(client=None),
        settings,
        reader,
        RetrieveRequest(
            kb_ids=[kb_id],
            queries=[{"q": "量子计算算法"}],
            strategy="sparse",
            rerank=mode != "disabled",
        ),
        uuid4(),
    )
    assert not result.evidence
    assert result.decision == ("reject" if mode == "low_score" else "unavailable")
    assert result.query_decisions[0]["covered"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"timestamp": 0},
        {"build_revision": "older"},
        {"pdf_ingestion": False},
        {"task_kinds": ["system.probe"]},
    ],
)
def test_processing_readiness_requires_a_live_compatible_pdf_worker(tmp_path, change):
    settings = Settings(_env_file=None, asset_root=str(tmp_path), build_revision="current")
    path = heartbeat_path(settings)
    path.parent.mkdir(parents=True)
    row = {
        "timestamp": time.time(),
        "build_revision": "current",
        "task_kinds": list(SUPPORTED_KINDS),
        "pdf_ingestion": True,
    }
    path.write_text(json.dumps(row))
    assert processing_status(settings)["status"] == "ready"
    path.write_text(json.dumps(row | change))
    assert processing_status(settings)["status"] == "not_ready"


def test_scene_derivation_additions_and_ambiguous_floors_are_reviewable():
    text = """### [通用][一楼] 接待大厅
[布局]问诊大厅及环形走廊
入口是挂号大厅 走廊是展示区
### [通用][二楼] 问诊中心
[布局]问诊大厅及环形走廊
物理治疗诊疗室 新增康复诊疗室
### [通用][五楼] 数据中心
[布局]数据中心大厅
"""
    scenes = derive_scenes(text)
    assert scenes["问诊大厅"] is None
    assert scenes["新增康复诊疗室"] == 2 and scenes["数据中心大厅"] == 5


async def test_scene_review_release_binding_and_revocation_are_atomic(database, tenant):
    settings = Settings(_env_file=None)
    actor = ActorContext(tenant, "reviewer", uuid4())
    reader = Principal(tenant, "reader", False, ("shared",), "patient")
    async with database.sessions.begin() as session:
        kb = KnowledgeBase(
            tenant_id=tenant, code="hospital_kb", scope_key="shared", hospital_id="hospital-test"
        )
        session.add(kb)
        await session.flush()
        documents = DocumentService(session, actor)
        source, _ = await documents.create_document(
            kb.id, title="医院场景", source_instance_id="test", external_key="hospital-scene-guide"
        )
        version, _ = await documents.create_version(
            source.id,
            VersionInput(
                filename="guide.md",
                content="### [通用][一楼] 接待大厅\n[布局]问诊大厅及环形走廊\n",
                source_locator="hospital/guide",
                effective_at=datetime.now(UTC) - timedelta(days=1),
                audiences=["patient"],
            ),
        )
        await documents.submit_for_review(version.id, expected_row_version=version.row_version)
        await ReviewService(session, actor).approve_version(
            version.id,
            expected_row_version=version.row_version,
            expected_content_hash=version.content_hash,
            expected_metadata_hash=version.metadata_hash,
            reason="test fixture",
        )
        snapshot = await propose(session, actor, kb.id, version.id, "测试医院", "测试医院")
        with pytest.raises(DocumentServiceError, match="review_required"):
            await create_release(session, settings, actor, kb.id, [version.id])
        await approve(
            session, actor, snapshot.id, await catalog_hash(session, snapshot), "review room names"
        )
        release, _ = await create_release(session, settings, actor, kb.id, [version.id])
        assert release.scene_snapshot_id == snapshot.id
        release.status = "ready"
        await activate_release(session, actor, release.id, "activate fixture")
        await session.flush()
        catalog = await published_catalogs(session, reader)
        assert catalog["测试医院"]["scenes"] == {"接待大厅": 1, "问诊大厅": 1}
        assert catalog["测试医院"]["source_version_id"] == str(version.id)
        stranger = Principal("another-tenant", "reader", False, ("shared",), "patient")
        assert await published_catalogs(session, stranger) == {}
        second, _ = await documents.create_version(
            source.id,
            VersionInput(
                filename="guide2.md",
                content="### [通用][一楼] 接待大厅\n新增康复诊疗室\n",
                source_locator="hospital/guide",
                effective_at=datetime.now(UTC) - timedelta(days=1),
                audiences=["patient"],
            ),
        )
        await documents.submit_for_review(second.id, expected_row_version=second.row_version)
        await ReviewService(session, actor).approve_version(
            second.id,
            expected_row_version=second.row_version,
            expected_content_hash=second.content_hash,
            expected_metadata_hash=second.metadata_hash,
            reason="updated fixture",
        )
        pending = await session.scalar(
            select(SceneSnapshot).where(SceneSnapshot.source_hash == second.content_hash)
        )
        assert pending.status == "draft"
        assert (await published_catalogs(session, reader))["测试医院"]["snapshot_id"] == str(
            snapshot.id
        )
        version.revoked_at = datetime.now(UTC)
        await session.flush()
        assert await published_catalogs(session, reader) == {}
