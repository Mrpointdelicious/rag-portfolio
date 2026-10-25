from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from rag_portfolio.adapters.ingestion import asset_path, digest, parse_source
from rag_portfolio.adapters.llama_vector import SparseEncoder, rrf_fusion
from rag_portfolio.config import Settings
from rag_portfolio.services.retrieval import pack_evidence
from rag_portfolio.services.retrieval_algorithms import Candidate


async def test_embedding_build_batches_misses_and_reuses_revision_cache(tmp_path):
    from rag_portfolio.services.releases import cached_embeddings

    settings = Settings(_env_file=None, asset_root=str(tmp_path), embedding_dimension=2)
    adapter = SimpleNamespace(embed_documents=AsyncMock(return_value=[[1.0, 2.0], [3.0, 4.0]]))
    first = await cached_embeddings(adapter, settings, "model-a", ["first", "second"])
    cached = await cached_embeddings(adapter, settings, "model-a", ["second", "first"])
    adapter.embed_documents.assert_awaited_once_with(["first", "second"])
    assert first == [[1.0, 2.0], [3.0, 4.0]] and cached == list(reversed(first))
    await cached_embeddings(adapter, settings, "model-b", ["first", "second"])
    assert adapter.embed_documents.await_count == 2


async def test_query_adapter_uses_installed_llamaindex_types_and_version_filter(monkeypatch):
    from dataclasses import asdict

    from llama_index.core.vector_stores.types import VectorStoreQueryMode

    from rag_portfolio.adapters import llama_vector

    store = SimpleNamespace(aquery=AsyncMock(return_value="result"))
    monkeypatch.setattr(llama_vector, "vector_store", lambda *args: store)
    version_id, release_id = uuid4(), uuid4()
    release = SimpleNamespace(
        id=release_id,
        tenant_id="portfolio",
        collection_name="release",
        manifest_json={"sparse": asdict(SparseEncoder.build(["康复"]))},
    )
    assert (
        await llama_vector.query_candidates(None, release, "康复", None, [version_id], "sparse", 10)
        == "result"
    )
    query = store.aquery.await_args.args[0]
    assert query.mode == VectorStoreQueryMode.SPARSE
    assert any(
        f.key == "version_id" and f.value == [str(version_id)] for f in query.filters.filters
    )


def test_ingestion_reuses_cache_but_rebuilds_when_chunk_configuration_changes(tmp_path):
    settings = Settings(_env_file=None, asset_root=str(tmp_path / "assets"))
    path = tmp_path / "source.md"
    raw = ("# Stroke\n\n脑卒中康复训练与步态评估。Evidence and rehabilitation.\n" * 40).encode()
    path.write_bytes(raw)
    first = parse_source(settings, path, path.name, digest(raw))
    cached = parse_source(settings, path, path.name, digest(raw))
    changed = parse_source(
        settings.model_copy(update={"ingestion_chunk_size": 256}), path, path.name, digest(raw)
    )
    assert not first.cache_hit and cached.cache_hit
    assert cached.chunks == first.chunks
    assert changed.revision != first.revision
    assert len(changed.chunks) > len(first.chunks)
    assert first.text == raw.decode()  # Do not concatenate overlapping chunks as source text.
    path.write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        parse_source(settings, path, path.name, digest(raw))


def test_pdf_rechunk_reuses_document_parse_and_preserves_page_anchors(tmp_path, monkeypatch):
    pytest.importorskip("docling_core")
    from docling_core.types.doc import (
        BoundingBox,
        DocItemLabel,
        DoclingDocument,
        ProvenanceItem,
        Size,
    )

    from rag_portfolio.adapters import ingestion

    document = DoclingDocument(name="source")
    document.add_page(page_no=1, size=Size(width=100, height=100))
    body = "Evidence and rehabilitation. " * 150
    document.add_text(
        label=DocItemLabel.TEXT,
        text=body,
        prov=ProvenanceItem(
            page_no=1, bbox=BoundingBox(l=0, t=0, r=100, b=100), charspan=(0, len(body))
        ),
    )
    calls = []

    def convert(*args):
        calls.append(True)
        return document

    monkeypatch.setattr(ingestion, "_convert_pdf", convert)
    path = tmp_path / "source.pdf"
    path.write_bytes(b"source bytes; conversion boundary stubbed")
    settings = Settings(_env_file=None, asset_root=str(tmp_path / "assets"))
    first = parse_source(settings, path, path.name, digest(path.read_bytes()))
    changed = parse_source(
        settings.model_copy(update={"ingestion_chunk_size": 256}),
        path,
        path.name,
        digest(path.read_bytes()),
    )
    assert len(calls) == 1
    assert len(changed.chunks) > len(first.chunks)
    assert first.text == document.export_to_markdown()
    assert all(c.provenance["anchors"][0]["page"] == 1 for c in changed.chunks)


def test_table_splitting_preserves_rows_and_repeats_headers():
    from rag_portfolio.adapters.ingestion import _split_table

    header = "| Name | Evidence |\n| --- | --- |"
    rows = [f"| Trial {i} | {'evidence ' * 10}|" for i in range(20)]
    chunks = _split_table(header + "\n" + "\n".join(rows), 80)
    assert len(chunks) > 1
    assert all(chunk.startswith(header) for chunk in chunks)
    assert [line for chunk in chunks for line in chunk.splitlines()[2:]] == rows


def test_asset_keys_cannot_escape_storage_root(tmp_path):
    settings = Settings(_env_file=None, asset_root=str(tmp_path))
    with pytest.raises(ValueError, match="invalid_asset_key"):
        asset_path(settings, "../outside")


def test_sparse_weights_prefer_specific_match_and_support_chinese():
    encoder = SparseEncoder.build(["脑卒中康复 步态训练", "脑卒中 评估", "unrelated text"])
    indexes, values = encoder.documents(["脑卒中康复 步态训练", "脑卒中 评估"])
    query_ids, _ = encoder.queries(["步态训练"])
    scores = [
        sum(value for index, value in zip(ids, weights, strict=True) if index in query_ids[0])
        for ids, weights in zip(indexes, values, strict=True)
    ]
    assert scores[0] > scores[1] == 0


def test_rrf_rewards_agreement_without_duplicate_nodes():
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores import VectorStoreQueryResult

    a, b, c = [TextNode(id_=str(uuid4()), text=text) for text in ["a", "b", "c"]]
    result = rrf_fusion(
        VectorStoreQueryResult(nodes=[a, b], similarities=[1, 0.5]),
        VectorStoreQueryResult(nodes=[b, c], similarities=[100, 10]),
        top_k=3,
    )
    assert result.ids[0] == b.node_id
    assert len(set(result.ids)) == 3


def test_budget_preserves_atomic_chunks_and_source_anchors():
    cid, vid, did, rid = [uuid4() for _ in range(4)]
    text = "reliable evidence"
    chunk = SimpleNamespace(
        id=cid,
        text=text,
        content_hash=digest(text.encode()),
        section_path="Methods",
        provenance_json={"anchors": [{"page": 2, "bbox": {"l": 1, "t": 2}}, {"page": 3}]},
    )
    version = SimpleNamespace(id=vid, metadata_json={"source_locator": "paper.pdf"})
    source = SimpleNamespace(id=did, title="Paper", external_key="paper")
    rows = {cid: (chunk, version, source)}
    assert pack_evidence([Candidate(cid, 1)], rows, {cid: rid}, 1, 5)[0] == []
    result, used = pack_evidence([Candidate(cid, 1)], rows, {cid: rid}, 10, 5)
    assert result[0].text == text
    assert result[0].page == 2 and len(result[0].anchors) == 2 and used == 3
