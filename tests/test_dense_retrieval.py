from datetime import UTC, datetime
from uuid import uuid4

import pytest

from rag_portfolio.config import Settings
from rag_portfolio.db.models import DocumentChunk, DocumentVersion, KnowledgeBase, SourceDocument
from rag_portfolio.services.dense_retrieval import (
    EMBEDDING_INPUT_VERSION,
    DenseRetrievalError,
    build_embedding_revision,
    build_embedding_text,
    build_qdrant_payload,
    hydrate_chunks,
    load_approved_corpus,
)


def test_embedding_input_trims_and_omits_empty_parts_without_deduplication():
    assert build_embedding_text(" 标题 ", " 章节 ", " 正文\n") == "标题\n\n章节\n\n正文"
    assert build_embedding_text(" 标题 ", " ", " 标题 ") == "标题\n\n标题"
    assert build_embedding_text("", "", "正文") == "正文"
    assert build_embedding_text(" ", "", "\n") == ""


def test_revision_records_provider_model_dimension_output_and_input_version():
    config = Settings(
        _env_file=None,
        embedding_provider="alibaba_dashscope",
        embedding_model="qwen3.7-text-embedding",
        embedding_dimension=1024,
        embedding_output_type="dense",
    )
    assert build_embedding_revision(config) == (
        "alibaba_dashscope:qwen3.7-text-embedding:d1024:dense:title-section-text-v1"
    )
    for field, value in (
        ("embedding_provider", "other"),
        ("embedding_model", "other"),
        ("embedding_dimension", 512),
        ("embedding_output_type", "other"),
    ):
        assert build_embedding_revision(config.model_copy(update={field: value})) != (
            build_embedding_revision(config)
        )


def test_payload_contains_only_chunk_identity_and_index_validation_metadata():
    document = SourceDocument(id=uuid4(), title="title")
    version = DocumentVersion(id=uuid4(), kb_id=uuid4())
    chunk = DocumentChunk(
        id=uuid4(),
        tenant_id="test",
        ordinal=6,
        section_path="二楼",
        text="private text",
        chunker_version="simple-v1:markdown:c800:o80",
        content_hash="a" * 64,
    )
    assert build_qdrant_payload(document, version, chunk, "revision") == {
        "tenant_id": "test",
        "kb_id": str(version.kb_id),
        "document_id": str(document.id),
        "version_id": str(version.id),
        "chunk_id": str(chunk.id),
        "ordinal": 6,
        "section_path": "二楼",
        "chunker_version": "simple-v1:markdown:c800:o80",
        "content_hash": "a" * 64,
        "embedding_revision": "revision",
        "embedding_input_version": EMBEDDING_INPUT_VERSION,
    }


@pytest.fixture
async def corpus_rows(database, tenant):
    async with database.sessions.begin() as session:
        kb = KnowledgeBase(tenant_id=tenant, scope_key="shared", code="hospital_kb")
        session.add(kb)
        await session.flush()
        document = SourceDocument(
            tenant_id=tenant,
            kb_id=kb.id,
            title="test hospital",
            source_system="test",
            source_instance_id="test",
            external_key="test-hospital",
        )
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            tenant_id=tenant,
            document_id=document.id,
            kb_id=kb.id,
            revision=2,
            normalized_text="first\n\nsecond",
            content_hash="a" * 64,
            metadata_hash="b" * 64,
            metadata_json={"format": "markdown"},
            audiences=["internal"],
            review_status="approved",
            reviewed_by="test-reviewer",
            reviewed_at=datetime.now(UTC),
            effective_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()
        # Insert out of ordinal order to exercise SQL ordering rather than insertion order.
        for ordinal in (1, 0):
            session.add(
                DocumentChunk(
                    tenant_id=tenant,
                    version_id=version.id,
                    chunker_version="test-chunker",
                    ordinal=ordinal,
                    section_path=f"section {ordinal}",
                    text=f"text {ordinal}",
                    content_hash="c" * 64,
                    token_count=2,
                )
            )
    return version.id


@pytest.mark.integration
async def test_load_orders_chunks_and_hydrate_preserves_point_order(database, tenant, corpus_rows):
    async with database.sessions() as session:
        corpus = await load_approved_corpus(session, tenant, corpus_rows)
    assert [chunk.ordinal for chunk in corpus.chunks] == [0, 1]
    ids = [chunk.id for chunk in reversed(corpus.chunks)]
    async with database.sessions() as session:
        rows = await hydrate_chunks(session, corpus, ids)
        assert [chunk.id for _, chunk in rows] == ids
        assert [chunk.text for _, chunk in rows] == ["text 1", "text 0"]
        with pytest.raises(DenseRetrievalError, match="Hydration failed"):
            await hydrate_chunks(session, corpus, [uuid4()])
        with pytest.raises(DenseRetrievalError, match="Duplicate"):
            await hydrate_chunks(session, corpus, [ids[0], ids[0]])
        with pytest.raises(DenseRetrievalError, match="not found"):
            await load_approved_corpus(session, "another-tenant", corpus_rows)


@pytest.mark.integration
@pytest.mark.parametrize("state", ["pending", "rejected", "draft", "revoked"])
async def test_load_and_hydrate_reject_unapproved_or_revoked_version(
    database, tenant, corpus_rows, state
):
    async with database.sessions() as session:
        corpus = await load_approved_corpus(session, tenant, corpus_rows)
    async with database.sessions.begin() as session:
        version = await session.get(DocumentVersion, corpus_rows)
        if state == "revoked":
            version.revoked_at = datetime.now(UTC)
        else:
            version.review_status = state
    async with database.sessions() as session:
        with pytest.raises(DenseRetrievalError, match="approved and not revoked"):
            await load_approved_corpus(session, tenant, corpus_rows)
        with pytest.raises(DenseRetrievalError, match="Hydration failed"):
            await hydrate_chunks(session, corpus, [corpus.chunks[0].id])
