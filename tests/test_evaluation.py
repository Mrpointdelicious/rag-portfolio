import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient

from rag_portfolio.adapters.embedding import EmbeddingAdapterError
from rag_portfolio.adapters.vector import QdrantConnection
from rag_portfolio.config import Settings
from rag_portfolio.db.models import DocumentChunk, DocumentVersion, KnowledgeBase, SourceDocument
from rag_portfolio.evaluation.corpus import (
    CHUNKER_VERSION,
    DOCUMENT_AUDIENCES,
    HOSPITAL_VERSION_ID,
    CorpusSnapshot,
    EvaluationError,
    hydrate_frozen_chunks,
    load_frozen_corpus,
    make_snapshot,
    read_snapshot,
    versions_from_import_report,
)
from rag_portfolio.evaluation.retrieval import (
    COLLECTION,
    DEFAULT_SUITE_PATH,
    EvalCase,
    GoldRef,
    aggregate_metrics,
    case_result,
    embed_documents_batched,
    read_suite,
    resolve_gold,
    run_evaluation,
    validate_index,
    write_report,
)
from rag_portfolio.services.chunking import text_hash
from rag_portfolio.services.dense_retrieval import ApprovedCorpus
from rag_portfolio.services.documents import metadata_hash


@pytest.fixture
def corpora(tenant):
    kb_id = uuid4()
    result = []
    now = datetime(2026, 10, 4, tzinfo=UTC)
    for key, audiences in DOCUMENT_AUDIENCES.items():
        doc = SourceDocument(
            id=uuid4(),
            tenant_id=tenant,
            kb_id=kb_id,
            title=f"Synthetic {key}",
            source_system="test",
            source_instance_id="test-eval",
            external_key=key,
        )
        version_id = HOSPITAL_VERSION_ID if key == "hospital-scene-guide" else uuid4()
        metadata = {"format": "markdown", "title": doc.title}
        text = f"Synthetic document {key}\n"
        version = DocumentVersion(
            id=version_id,
            tenant_id=tenant,
            document_id=doc.id,
            kb_id=kb_id,
            revision=2 if key == "hospital-scene-guide" else 1,
            normalized_text=text,
            content_hash=text_hash(text),
            metadata_json=metadata,
            metadata_hash=metadata_hash(metadata, sorted(audiences), now, None),
            audiences=sorted(audiences),
            review_status="approved",
            revoked_at=None,
            effective_at=now,
            reviewed_at=now,
            reviewed_by="synthetic-reviewer",
            expires_at=None,
        )
        chunks = []
        for ordinal in range(10 if key == "hospital-scene-guide" else 2):
            content = f"Synthetic evidence {key} {ordinal}"
            chunks.append(
                DocumentChunk(
                    id=uuid4(),
                    tenant_id=tenant,
                    version_id=version.id,
                    chunker_version=CHUNKER_VERSION,
                    ordinal=ordinal,
                    section_path=f"Heading {ordinal}",
                    text=content,
                    content_hash=text_hash(content),
                    token_count=5,
                )
            )
        result.append(ApprovedCorpus(doc, version, chunks))
    return result


def snapshot_for(corpora):
    return make_snapshot(corpora, scope_key="shared")


def test_snapshot_roundtrip_and_unique_five_documents(corpora, tmp_path):
    snapshot = snapshot_for(corpora)
    assert len(snapshot.documents) == 5 and snapshot.chunk_count == 18
    assert set(snapshot.version_ids) == set(DOCUMENT_AUDIENCES)
    path = tmp_path / "corpus.json"
    path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    assert read_snapshot(path) == snapshot
    assert "Synthetic evidence" not in path.read_text(encoding="utf-8")
    with pytest.raises(EvaluationError, match="unique"):
        snapshot_for([*corpora[:4], corpora[0]])
    raw = snapshot.model_dump(mode="json")
    raw["documents"][0]["chunks"][0]["chunk_id"] = raw["documents"][1]["chunks"][0]["chunk_id"]
    with pytest.raises(ValidationError, match="Duplicate chunk"):
        CorpusSnapshot.model_validate(raw)


@pytest.mark.parametrize("state", ["draft", "pending", "rejected", "revoked"])
def test_snapshot_rejects_unapproved_versions(corpora, state):
    version = corpora[1].version
    if state == "revoked":
        version.revoked_at = datetime.now(UTC)
    else:
        version.review_status = state
    with pytest.raises(EvaluationError, match="approved"):
        snapshot_for(corpora)


@pytest.mark.parametrize("problem", ["missing", "multiple"])
def test_snapshot_rejects_missing_chunks_and_multiple_chunkers(corpora, problem):
    if problem == "missing":
        corpora[1].chunks.clear()
    else:
        corpora[1].chunks[0].chunker_version = "other-chunker"
    with pytest.raises(EvaluationError, match="one chunker"):
        snapshot_for(corpora)


@pytest.mark.parametrize("field", ["tenant_id", "kb_id"])
def test_snapshot_rejects_cross_tenant_or_kb(corpora, field):
    setattr(corpora[1].version, field, "other" if field == "tenant_id" else uuid4())
    with pytest.raises(EvaluationError, match="one tenant and one KB"):
        snapshot_for(corpora)


def test_hospital_revision_and_id_must_stay_fixed(corpora):
    corpora[0].version.revision = 3
    with pytest.raises(EvaluationError, match="hospital revision"):
        snapshot_for(corpora)
    corpora[0].version.revision = 2
    corpora[0].version.id = uuid4()
    for chunk in corpora[0].chunks:
        chunk.version_id = corpora[0].version.id
    with pytest.raises(EvaluationError, match="hospital revision"):
        snapshot_for(corpora)


def test_import_report_pins_exact_ids_and_rejects_duplicate_documents(corpora, tmp_path):
    rows = [
        {
            "external_key": c.document.external_key,
            "version_id": str(c.version.id),
            "status": "success",
        }
        for c in corpora[1:]
    ]
    path = tmp_path / "import_report.json"
    path.write_text(json.dumps({"documents": rows}), encoding="utf-8")
    versions = versions_from_import_report(path)
    assert versions == {c.document.external_key: c.version.id for c in corpora}
    rows[-1] = rows[0]
    path.write_text(json.dumps({"documents": rows}), encoding="utf-8")
    with pytest.raises(EvaluationError, match="four successful"):
        versions_from_import_report(path)


@pytest.fixture
async def seeded(database, corpora):
    async with database.sessions() as session:
        session.add(
            KnowledgeBase(
                id=corpora[0].version.kb_id,
                tenant_id=corpora[0].version.tenant_id,
                scope_key="shared",
                code="hospital_kb",
            )
        )
        await session.flush()
        session.add_all([c.document for c in corpora])
        await session.flush()
        session.add_all([c.version for c in corpora])
        await session.flush()
        session.add_all([chunk for c in corpora for chunk in reversed(c.chunks)])
        await session.flush()
        yield session, snapshot_for(corpora), corpora
        await session.rollback()


@pytest.mark.integration
async def test_multi_version_hydration_uses_qdrant_order(seeded):
    session, snapshot, corpora = seeded
    selected = [corpora[3].chunks[1], corpora[0].chunks[4], corpora[1].chunks[0]]
    rows = await hydrate_frozen_chunks(session, snapshot, [chunk.id for chunk in selected])
    assert [chunk.id for _, _, chunk in rows] == [chunk.id for chunk in selected]
    assert len({version.id for _, version, _ in rows}) == 3
    assert [chunk.text for _, _, chunk in rows] == [chunk.text for chunk in selected]
    with pytest.raises(EvaluationError, match="unique chunk IDs"):
        await hydrate_frozen_chunks(session, snapshot, [uuid4()])


@pytest.mark.integration
@pytest.mark.parametrize(
    "change", ["pending", "revoked", "text", "hash", "both", "section", "title", "version"]
)
async def test_database_drift_is_detected_even_outside_current_hits(seeded, change):
    session, snapshot, corpora = seeded
    affected = corpora[-1]
    if change == "pending":
        affected.version.review_status = "pending"
    elif change == "revoked":
        affected.version.revoked_at = datetime.now(UTC)
    elif change in {"text", "hash", "both"}:
        chunk = affected.chunks[0]
        if change in {"text", "both"}:
            chunk.text = "Altered synthetic text"
        if change in {"hash", "both"}:
            chunk.content_hash = text_hash("Altered synthetic text")
    elif change == "section":
        affected.chunks[0].section_path = "Changed heading"
    elif change == "title":
        affected.document.title = "Changed title"
    else:
        affected.version.normalized_text += "changed"
    await session.flush()
    with pytest.raises(EvaluationError):
        await load_frozen_corpus(session, snapshot)
    with pytest.raises(EvaluationError):
        await hydrate_frozen_chunks(session, snapshot, [corpora[0].chunks[0].id])


def eval_case(*, category="exact_fact", refs=None, **kwargs):
    return EvalCase(
        id="E01",
        query="Synthetic question",
        category=category,
        answerable=category != "no_answer",
        scored=category not in {"no_answer", "source_conflict"},
        gold_refs=refs
        if refs is not None
        else (GoldRef(external_key="general-patient-guide", section_contains="Heading 0"),),
        **kwargs,
    )


def test_gold_refs_resolve_unique_and_multiple_chunks(corpora):
    assert resolve_gold(eval_case(), corpora) == {corpora[1].chunks[0].id}
    case = eval_case(
        category="multi_gold",
        refs=(
            GoldRef(external_key="general-patient-guide", section_contains="Heading 0"),
            GoldRef(
                external_key="yueyang-doctor-guide",
                section_contains="Heading 1",
                text_contains="evidence",
            ),
        ),
    )
    assert resolve_gold(case, corpora) == {corpora[1].chunks[0].id, corpora[4].chunks[1].id}


def test_unresolved_and_ambiguous_gold_fail(corpora):
    with pytest.raises(EvaluationError, match="matched 0"):
        resolve_gold(
            eval_case(refs=(GoldRef(external_key="missing", section_contains="Heading"),)), corpora
        )
    corpora[1].chunks[1].section_path = "Heading 0"
    with pytest.raises(EvaluationError, match="matched 2"):
        resolve_gold(eval_case(), corpora)


def hits_at(gold, rank, *, top1_document="other", top1_audiences=("doctor",)):
    return [
        {
            "rank": i,
            "score": 1 / i,
            "chunk_id": str(gold if i == rank else uuid4()),
            "external_key": top1_document if i == 1 else "general-patient-guide",
            "audiences": list(top1_audiences) if i == 1 else ["patient"],
            "section_path": "Synthetic heading",
            "ordinal": i,
            "text_preview": "Synthetic preview",
        }
        for i in range(1, 6)
    ]


def test_recall_mrr_document_hit_and_categories_exclude_observations():
    gold = uuid4()
    results = []
    for index, rank in enumerate((1, 3, 5, None)):
        case = eval_case(category="patient_role" if index < 2 else "exact_fact")
        # Same document can be Top-1 even when the exact Gold chunk is ranked third.
        hits = hits_at(gold, rank, top1_document="general-patient-guide" if index < 2 else "other")
        results.append(case_result(case, {gold}, hits, {"patient"}))
    for category, refs in (("no_answer", ()), ("source_conflict", eval_case().gold_refs)):
        observation = case_result(
            eval_case(category=category, refs=refs), {gold}, hits_at(gold, 1), {"patient"}
        )
        assert observation["metrics"] is None and observation["excluded_from_quality_metrics"]
        assert observation["top1_score"] == 1
        results.append(observation)
    summary = aggregate_metrics(results)
    assert summary["overall"] == {
        "count": 4,
        "Recall@1": 0.25,
        "Recall@3": 0.5,
        "Recall@5": 0.75,
        "MRR@5": pytest.approx((1 + 1 / 3 + 1 / 5) / 4),
        "DocHit@1": 0.5,
    }
    assert set(summary["per_category"]) == {"patient_role", "exact_fact"}
    assert summary["per_category"]["patient_role"]["MRR@5"] == pytest.approx(2 / 3)


def test_multi_gold_uses_first_relevant_chunk_and_any_gold_document():
    first, second = uuid4(), uuid4()
    case = eval_case(
        category="multi_gold",
        refs=(
            GoldRef(external_key="general-patient-guide", section_contains="Heading"),
            GoldRef(external_key="yueyang-patient-guide", section_contains="Heading"),
        ),
    )
    hits = hits_at(second, 4, top1_document="yueyang-patient-guide")
    hits[1]["chunk_id"] = str(first)
    result = case_result(case, {first, second}, hits, {"patient"})
    assert result["gold_rank"] == 2
    assert result["metrics"] == {
        "Recall@1": 0,
        "Recall@3": 1,
        "Recall@5": 1,
        "MRR@5": 0.5,
        "DocHit@1": 1,
    }
    assert result["failures"] == ["gold_rank_2_5"]


def test_role_failure_distinguishes_wrong_document_from_wrong_role():
    gold = uuid4()
    case = eval_case(category="role_disambiguation")
    result = case_result(case, {gold}, hits_at(gold, None), {"patient"})
    assert result["failures"] == [
        "gold_miss_top5",
        "top1_wrong_document",
        "role_top1_wrong_document",
    ]
    same_role = case_result(
        case, {gold}, hits_at(gold, 3, top1_audiences=("patient",)), {"patient"}
    )
    assert same_role["failures"] == ["gold_rank_2_5", "top1_wrong_document"]


async def test_batched_embedding_45_texts_preserves_order():
    adapter = SimpleNamespace(
        embed_documents=AsyncMock(side_effect=lambda texts: [[float(text)] for text in texts])
    )
    assert await embed_documents_batched(adapter, [str(i) for i in range(45)], batch_size=20) == [
        [float(i)] for i in range(45)
    ]
    assert [len(call.args[0]) for call in adapter.embed_documents.call_args_list] == [20, 20, 5]


async def test_batched_embedding_stops_at_first_failure():
    adapter = SimpleNamespace(
        embed_documents=AsyncMock(
            side_effect=[[[1.0]] * 20, EmbeddingAdapterError("synthetic failure")]
        )
    )
    with pytest.raises(EmbeddingAdapterError, match="synthetic failure"):
        await embed_documents_batched(adapter, ["text"] * 45, batch_size=20)
    assert adapter.embed_documents.await_count == 2
    with pytest.raises(EvaluationError):
        await embed_documents_batched(adapter, ["text"], batch_size=0)


def test_suite_preserves_fixed_queries_and_exclusion_flags():
    suite = read_suite(DEFAULT_SUITE_PATH)
    assert len(suite.cases) == 29 and sum(case.scored for case in suite.cases) == 25
    assert [case.query for case in suite.cases] == [
        "岳阳医院场景总共有几层？",
        "二楼问诊中心右侧有哪些诊疗室？",
        "智能步道康复室在哪一层？",
        "四楼能做哪些类型的评估？",
        "五楼数据中心的大屏会展示哪些数据？",
        "岳阳医院一楼主要是什么区域？",
        "我想做记忆和认知方面的评估，应该去哪里？",
        "我想练智能步道，应该去几楼找？",
        "我想看患者流量、病例和诊疗数据，应该看哪一层的大屏？",
        "我怎样从岳阳医院场景回到社交岛？",
        "患者端右上角的两个按钮是什么？",
        "患者在哪里查看当前训练安排？",
        "患者端快速通道里面有哪些入口？",
        "患者怎么手动挂号？",
        "医生在哪里查看和管理设备？",
        "医生端快速通道包含哪些功能？",
        "医生怎样快速进入问诊？",
        "医生能不能直接用语音进入问诊？",
        "我是患者，怎么查看自己的就诊记录和训练报告？",
        "我是医生，为什么看不到患者端的立即挂号？",
        "我是患者，能使用“立即前往问诊”这个医生入口吗？",
        "我是患者，想快速就诊，应该从哪里进入？",
        "怎么去社交岛？",
        "怎么去二楼？",
        "岳阳医院有六楼吗？",
        "怎么修改登录密码？",
        "岳阳医院停车位怎么预约？",
        "怎么把训练报告导出成 PDF？",
        "患者端用语音挂号时应该说什么？",
    ]
    for category in ("no_answer", "source_conflict"):
        original = next(case for case in suite.cases if case.category == category)
        with pytest.raises(ValidationError):
            EvalCase.model_validate(original.model_dump() | {"scored": True})


@pytest.fixture
def fake_runtime(monkeypatch, corpora):
    import rag_portfolio.evaluation.retrieval as runtime

    @asynccontextmanager
    async def sessions():
        yield object()

    database = SimpleNamespace(sessions=sessions, close=AsyncMock())
    load = AsyncMock(return_value=corpora)
    by_id = {chunk.id: (c.document, c.version, chunk) for c in corpora for chunk in c.chunks}
    hydrate = AsyncMock(side_effect=lambda _session, _snapshot, ids: [by_id[key] for key in ids])
    adapter = SimpleNamespace(
        embed_documents=AsyncMock(side_effect=lambda texts: [[1.0] + [0.0] * 1023 for _ in texts]),
        embed_query=AsyncMock(return_value=[1.0] + [0.0] * 1023),
        close=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "Database", lambda _: database)
    monkeypatch.setattr(runtime, "EmbeddingAdapter", lambda _: adapter)
    monkeypatch.setattr(runtime, "load_frozen_corpus", load)
    monkeypatch.setattr(runtime, "hydrate_frozen_chunks", hydrate)
    client = AsyncQdrantClient(location=":memory:")
    connection = object.__new__(QdrantConnection)
    connection.client = client
    factory = Mock(return_value=connection)
    monkeypatch.setattr(runtime, "QdrantConnection", factory)
    suite = read_suite(DEFAULT_SUITE_PATH)
    # Test only synthetic evidence, never copy private corpus text into test fixtures.
    suite = suite.model_copy(
        update={
            "cases": tuple(
                case.model_copy(
                    update={
                        "gold_refs": tuple(
                            ref.model_copy(
                                update={"section_contains": "Heading 0", "text_contains": None}
                            )
                            for ref in case.gold_refs
                        )
                    }
                )
                for case in suite.cases
            )
        }
    )
    config = Settings(_env_file=None, tenant_id=corpora[0].version.tenant_id)
    return SimpleNamespace(
        settings=config,
        suite=suite,
        snapshot=snapshot_for(corpora),
        adapter=adapter,
        database=database,
        factory=factory,
        client=client,
        load=load,
    )


async def test_complete_run_writes_reports_and_excludes_conflict_and_no_answer(
    fake_runtime, tmp_path
):
    env = fake_runtime
    report = await run_evaluation(env.settings, env.suite, env.snapshot, recreate=True)
    assert report["point_count"] == report["chunk_count"] == 18
    assert report["query_count"] == 29 and report["scored_count"] == 25
    assert report["no_answer_count"] == 3 and report["source_conflict_count"] == 1
    assert report["overall"]["count"] == 25
    assert len(report["per_category"]) == 7
    conflict = report["cases"][-1]
    assert conflict["observation"] == "SOURCE_CONFLICT" and conflict["metrics"] is None
    assert all("text" in hit for hit in conflict["top5"])
    write_report(report, tmp_path)
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8")) == report
    markdown = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "SOURCE_CONFLICT" in markdown and "excluded_from_quality_metrics" in markdown
    assert len(list((tmp_path / "runs").glob("*.json"))) == 1
    env.adapter.close.assert_awaited_once()
    env.database.close.assert_awaited_once()


async def test_failed_embedding_run_never_creates_or_mutates_collection(fake_runtime):
    env = fake_runtime
    env.adapter.embed_documents.side_effect = EmbeddingAdapterError("synthetic provider failure")
    with pytest.raises(EmbeddingAdapterError):
        await run_evaluation(env.settings, env.suite, env.snapshot, recreate=True)
    env.factory.assert_not_called()
    assert not await env.client.collection_exists(COLLECTION)
    await env.client.close()
    env.adapter.close.assert_awaited_once()


async def test_index_validation_rejects_partial_index(fake_runtime):
    from qdrant_client import models

    env = fake_runtime
    connection = env.factory.return_value
    await env.client.create_collection(
        COLLECTION, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE)
    )
    candidate = models.PointStruct(
        id=str(uuid4()), vector=[1.0, 0.0, 0.0], payload={"chunk_id": "test"}
    )
    await validate_index(connection, [candidate], complete=False)
    with pytest.raises(EvaluationError, match="Incomplete index"):
        await validate_index(connection, [candidate], complete=True)
    await env.client.close()
