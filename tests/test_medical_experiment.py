"""Original-PDF provenance, domain isolation and multi-source evaluation boundaries."""

import copy
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient, models

from rag_portfolio.evaluation.experiments import EXPERIMENTS, medical_path
from rag_portfolio.evaluation.medical_corpus import (
    load_corpus,
    normalize_pages,
    page_chunks,
    quality_flags,
    text_digest,
)
from rag_portfolio.evaluation.medical_runner import (
    RETRIEVAL_CONFIG,
    EmbeddingCache,
    dense_search,
    index_corpus,
    validate_test_prerequisites,
    write_report,
)
from rag_portfolio.evaluation.medical_schema import MedicalFilters, MedicalSuite, case_metrics
from rag_portfolio.literature_backup import LiteratureError, backup_pdfs, file_hash


def inventory(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.7\noriginal fixture")
    checksum = file_hash(pdf)
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("nested/second.pdf", b"%PDF-1.7\nreserve fixture")
    import hashlib

    second = hashlib.sha256(b"%PDF-1.7\nreserve fixture").hexdigest()
    documents = [
        {"资料ID": key, "格式": ".pdf", "SHA256": sha, "标题": key, "PDF页数": 1}
        for key, sha in (("D001", checksum), ("D002", second))
    ]
    locations = [
        {
            "资料ID": "D001",
            "副本ID": "F001",
            "格式": ".pdf",
            "原始路径": str(pdf),
            "文件SHA256": checksum,
        },
        {
            "资料ID": "D001",
            "副本ID": "F002",
            "格式": ".pdf",
            "原始路径": str(pdf),
            "文件SHA256": checksum,
        },
        {
            "资料ID": "D002",
            "副本ID": "F003",
            "格式": ".pdf",
            "原始路径": str(archive),
            "ZIP成员路径": "nested/second.pdf",
            "文件SHA256": second,
        },
        {"资料ID": "D003", "副本ID": "F004", "格式": ".docx", "原始路径": "not-read.docx"},
    ]
    return {
        "domain": "medical_knowledge",
        "inventory_sha256": "a" * 64,
        "documents": documents,
        "locations": locations,
        "selected": [{"资料ID": "D001"}],
    }, pdf


def test_backup_all_pdf_aliases_and_zip_members_without_word_or_source_changes(tmp_path):
    data, source = inventory(tmp_path)
    before = source.read_bytes()
    dest = tmp_path / "doc"
    result = backup_pdfs(data, dest)
    assert (result["pdf_count"], result["source_location_count"]) == (2, 3)
    assert (result["selected_count"], result["unselected_count"]) == (1, 1)
    assert backup_pdfs(data, dest)["new_files"] == 0
    assert source.read_bytes() == before
    manifest = json.loads((dest / "backup_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["documents"][0]["aliases"]) == 2
    assert all(row["review_status"] == "not_approved" for row in manifest["documents"])
    assert len(list(dest.rglob("*.pdf"))) == 2


def test_backup_fails_on_source_or_existing_backup_drift(tmp_path):
    data, source = inventory(tmp_path)
    dest = tmp_path / "doc"
    backup_pdfs(data, dest)
    original_manifest = (dest / "backup_manifest.json").read_bytes()
    source.write_bytes(b"changed")
    with pytest.raises(LiteratureError, match="Source hash changed"):
        backup_pdfs(data, dest)
    assert (dest / "backup_manifest.json").read_bytes() == original_manifest
    target = next((dest / "selected").glob("*.pdf"))
    target.write_bytes(b"wrong backup")
    with pytest.raises(LiteratureError, match="Existing backup differs"):
        backup_pdfs(data, dest)


def test_medical_namespace_rejects_metaverse_and_escape_paths(tmp_path):
    root = tmp_path
    for path in [
        root / ".local/eval/hospital_dense_v1/report.json",
        root / ".local/medical_rehab_v1/../../eval/gold.json",
    ]:
        with pytest.raises(LiteratureError, match="namespace"):
            medical_path(root, path)
    medical_path(root, root / ".local/medical_rehab_v1/corpus/manifest.json")
    assert EXPERIMENTS["metaverse_v2"].domain == "metaverse_scenes"
    assert EXPERIMENTS["medical_rehab_v1"].domain == "medical_knowledge"
    with pytest.raises(ValidationError):
        MedicalSuite.model_validate(
            {
                "schema_version": 1,
                "domain": "metaverse_scenes",
                "experiment_id": "medical_rehab_v1",
                "cases": [],
            }
        )


def test_source_offsets_survive_overlap_and_conservative_normalization():
    raw = (
        "Journal Header\r\nnot suitable 0.5 mg\r\nvari\ufffeable and re\x02search\r\n"
        + "正文ABCD\n" * 180
    )
    normalized, logs = normalize_pages([raw] * 3, approved_margins=["Journal Header"])
    assert "not suitable 0.5 mg" in normalized[0]
    assert "vari-able" in normalized[0] and "re-search" in normalized[0]
    assert logs[0]["discretionary_hyphen_repairs"] == 2
    page = {
        "pdf_page": 2,
        "printed_page": "301",
        "text": normalized[0],
        "text_sha256": text_digest(normalized[0]),
        "parser": "fixture",
        "parser_version": "1",
    }
    doc = {
        "document_id": "D001",
        "title": "Original",
        "sha256": "a" * 64,
        "path": "selected/source.pdf",
        "metadata": {},
    }
    chunks = page_chunks(page, doc)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk["text"] == normalized[0][chunk["start_char"] : chunk["end_char"]]
        assert chunk["pdf_page_start"] == chunk["pdf_page_end"] == 2
        assert chunk["printed_page"] == "301"
        assert len(chunk["text"]) <= 800
    assert chunks == page_chunks(page, doc)
    assert "control_glyphs" in quality_flags("readable text" * 20 + "\x04")
    assert "noncharacter_glyphs" in quality_flags("readable text" * 20 + "\ufffe")


def test_multi_source_any_hit_is_not_complete_and_unknown_conditions_are_not_guessed():
    result = case_metrics(["a", "c"], [{"a", "b"}, {"z"}])
    assert result["hit_at_5"] == 1
    assert result["required_evidence_coverage"] == 0.5
    assert result["complete_evidence_at_5"] == 0
    assert case_metrics(["z", "a"], [{"a"}, {"z"}])["complete_evidence_at_5"] == 1
    assert not MedicalFilters(population="adult").matches({"population": None})
    assert MedicalFilters(stage="acute").matches({"stage": ["acute", "recovery"]})


@pytest.mark.asyncio
async def test_dense_index_does_not_accept_foreign_points_and_filters_before_topk():
    client = AsyncQdrantClient(":memory:")
    try:
        chunks = [
            {
                "chunk_id": str(uuid4()),
                "document_id": f"D{i:03}",
                "source_sha256": "a" * 64,
                "text_sha256": "b" * 64,
                "pdf_page_start": 1,
                "pdf_page_end": 1,
                "metadata": {"disease": disease},
            }
            for i, disease in enumerate(["stroke", "knee_osteoarthritis"], 1)
        ]
        name = "rag_eval_medical_rehab_v1_fixture"
        expected = await index_corpus(
            client, name, chunks, [[1.0, 0.0], [0.9, 0.1]], "c" * 64, "r", 2
        )
        hits = await dense_search(
            client, name, [1.0, 0.0], "c" * 64, "r", expected, {"disease": "knee_osteoarthritis"}
        )
        assert [str(hit.chunk_id) for hit in hits] == [chunks[1]["chunk_id"]]
        await client.upsert(
            name,
            points=[
                models.PointStruct(
                    id=str(uuid4()), vector=[1.0, 0.0], payload={"domain": "metaverse_scenes"}
                )
            ],
        )
        with pytest.raises(LiteratureError, match="Foreign"):
            await index_corpus(client, name, chunks, [[1.0, 0.0], [0.9, 0.1]], "c" * 64, "r", 2)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_embedding_cache_never_reuses_another_model_and_retains_partial_failure(tmp_path):
    class Adapter:
        calls = 0

        async def embed_documents(self, texts):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("provider unavailable")
            return [[1.0, 0.0] for _ in texts]

    path = tmp_path / "cache.json"
    cache = EmbeddingCache(path, "model_a", 2)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await cache.documents(Adapter(), [str(i) for i in range(21)])
    assert len(EmbeddingCache(path, "model_a", 2).values) == 20
    with pytest.raises(LiteratureError, match="another domain/model"):
        EmbeddingCache(path, "model_b", 2)
    with pytest.raises(LiteratureError, match="complete"):
        write_report({"domain": "medical_knowledge", "status": "partial"}, tmp_path / "reports")
    assert not (tmp_path / "reports").exists()


def test_sealed_test_rejects_incomplete_dev_and_changed_model_domain_corpus_or_config():
    root = Path(__file__).resolve().parents[1]
    from rag_portfolio.evaluation.medical_corpus import digest
    from rag_portfolio.evaluation.medical_schema import MedicalGold

    gold = MedicalGold.model_validate_json(
        (root / "eval/medical_rehab_v1/gold.json").read_text(encoding="utf-8")
    )
    dev = {
        "status": "complete",
        "domain": "medical_knowledge",
        "experiment_id": "medical_rehab_v1",
        "split": "dev",
        "corpus_fingerprint": gold.corpus_fingerprint,
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "embedding_revision": "fixed_model",
        "config": RETRIEVAL_CONFIG,
        "retrieval_config_fingerprint": digest(RETRIEVAL_CONFIG),
    }
    validate_test_prerequisites(dev, gold, "fixed_model")
    for field, wrong in (
        ("domain", "metaverse_scenes"),
        ("status", "partial"),
        ("corpus_fingerprint", "0" * 64),
        ("test_seal", "0" * 64),
        ("embedding_revision", "changed_model"),
        ("config", {**RETRIEVAL_CONFIG, "rrf_k": 1}),
    ):
        with pytest.raises(LiteratureError, match="test remains sealed"):
            validate_test_prerequisites({**dev, field: wrong}, gold, "fixed_model")


def test_gold_seal_and_original_page_mapping_are_verified_against_real_fixture_if_present():
    # The public unit suite is portable; local source-backed checks run only where
    # the user-authorized private PDFs are present. No provider calls are made.
    root = Path(__file__).resolve().parents[1]
    corpus_path = root / ".local/medical_rehab_v1/corpus/manifest.json"
    if not corpus_path.exists():
        pytest.skip("Private original-PDF fixture is local only")
    from rag_portfolio.evaluation.medical_schema import MedicalGold, read_suite, validate_gold

    manifest, pages, chunks = load_corpus(corpus_path, root / "doc")
    suite = read_suite(root / "eval/medical_rehab_v1/cases.json")
    gold = MedicalGold.model_validate_json(
        (root / "eval/medical_rehab_v1/gold.json").read_text(encoding="utf-8")
    )
    assert len(validate_gold(gold, suite, manifest, pages, chunks)) == 40
    assert (
        sum(case.split == "dev" for case in suite.cases),
        sum(case.split == "test" for case in suite.cases),
    ) == (28, 12)
    assert len(chunks) == manifest["chunk_count"]
    assert manifest["page_count"] == 102
    assert all((chunk["document_id"], chunk["pdf_page_start"]) != ("D173", 3) for chunk in chunks)
    changed = gold.model_copy(update={"test_seal": "0" * 64})
    with pytest.raises(LiteratureError, match="Sealed"):
        validate_gold(changed, suite, manifest, pages, chunks)
    changed_pages = copy.deepcopy(pages)
    ref = next(
        ref for case in gold.cases for group in case.required_groups for ref in group.alternatives
    )
    page = next(
        row
        for row in changed_pages
        if (row["document_id"], row["pdf_page"]) == (ref.document_id, ref.pdf_page)
    )
    page["text"] = "x" * len(page["text"])
    with pytest.raises(LiteratureError, match="Gold span changed"):
        validate_gold(gold, suite, manifest, changed_pages, chunks)
