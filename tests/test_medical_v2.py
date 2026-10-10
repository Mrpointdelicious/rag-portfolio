import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models

from rag_portfolio.config import Settings
from rag_portfolio.evaluation.medical_corpus import load_corpus
from rag_portfolio.evaluation.medical_schema import validate_gold
from rag_portfolio.evaluation.medical_v2 import (
    GoldV2,
    Reranker,
    SuiteV2,
    balanced,
    deduplicate,
    index_v2,
    resolve_sources,
    select_variant,
    v2_path,
    validate_rerank_results,
    validate_selection,
)
from rag_portfolio.evaluation.retrievers import Candidate
from rag_portfolio.literature_backup import LiteratureError


def candidate(number):
    return Candidate(UUID(int=number), float(number))


def test_query_only_routing_preserves_content_and_unknown_query():
    aliases = {"D001": ["Standing Balance Guide"], "D003": ["2024中西医结合康复指南"]}
    assert resolve_sources("Which evidence tool?", aliases) == ([], "Which evidence tool?")
    sources, content = resolve_sources(
        "Compare Standing Balance Guide with 2024中西医结合康复指南: what evidence tool?", aliases
    )
    assert sources == ["D001", "D003"]
    assert "what evidence tool?" in content
    assert "Standing" not in content
    assert resolve_sources("2024指南", aliases)[0] == []


def test_balanced_sources_keep_rank_order_without_gold():
    hits = [candidate(n) for n in range(1, 6)]
    lookup = {
        str(c.chunk_id): {"document_id": "D001" if n < 4 else "D003"} for n, c in enumerate(hits, 1)
    }
    result = balanced(hits, ["D003", "D001"], lookup)
    assert result == [hits[3], hits[0], hits[4], hits[1], hits[2]]
    assert balanced(hits, [], lookup) == hits


def test_dedup_keeps_different_measurements_negation_and_sources():
    hits = [candidate(n) for n in range(1, 5)]
    rows = [
        {
            "document_id": "D001",
            "pdf_page_start": 1,
            "start_char": 0,
            "end_char": 30,
            "text": "Do not use the device without an assessment.",
        },
        {
            "document_id": "D001",
            "pdf_page_start": 1,
            "start_char": 0,
            "end_char": 30,
            "text": "Do not use the device without an assessment.",
        },
        {
            "document_id": "D001",
            "pdf_page_start": 1,
            "start_char": 0,
            "end_char": 30,
            "text": "Use the device without an assessment.",
        },
        {
            "document_id": "D003",
            "pdf_page_start": 1,
            "start_char": 0,
            "end_char": 30,
            "text": "Do not use the device without an assessment.",
        },
    ]
    lookup = {str(c.chunk_id): row for c, row in zip(hits, rows, strict=True)}
    assert deduplicate(hits, lookup) == [hits[0], hits[2], hits[3]]


@pytest.mark.parametrize(
    "rows",
    [
        [{"index": 0, "relevance_score": 0.9}],
        [{"index": 0, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}],
        [{"index": 0, "relevance_score": float("nan")}, {"index": 1, "relevance_score": 0.2}],
        [{"index": True, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.2}],
    ],
)
def test_reranker_rejects_missing_duplicate_and_invalid_scores(rows):
    with pytest.raises(LiteratureError):
        validate_rerank_results(rows, 2)


@pytest.mark.asyncio
async def test_real_rerank_contract_cache_and_body_redaction(tmp_path):
    settings = Settings(
        _env_file=None,
        embedding_base_url="https://example.test/api/v1",
        embedding_api_key="super-secret",
    )
    hits = [candidate(1), candidate(2)]
    lookup = {
        str(c.chunk_id): {"title": "PDF", "section_path": "scope", "text": str(n)}
        for n, c in enumerate(hits)
    }
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/api/v1/services/rerank/text-rerank/text-rerank"
        assert body["model"] == "qwen3.7-text-rerank"
        assert body["parameters"]["top_n"] == 2
        assert body["input"]["query"] == "Which?"
        assert "required_groups" not in body
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.8},
                        {"index": 0, "relevance_score": 0.1},
                    ]
                },
                "usage": {"total_tokens": 12},
                "request_id": "request-123",
            },
        )

    reranker = Reranker(settings, tmp_path / "cache.json", transport=httpx.MockTransport(respond))
    try:
        first, audit = await reranker.rerank("Which?", hits, lookup)
        second, _ = await reranker.rerank("Which?", hits, lookup)
        assert first == second
        assert [c.chunk_id for c in first] == [hits[1].chunk_id, hits[0].chunk_id]
        assert audit["request_id"] == "request-123"
        assert reranker.calls == 1 and reranker.cache_hits == 1 and len(requests) == 1
    finally:
        await reranker.close()
    bad = Reranker(
        settings,
        tmp_path / "other.json",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(403, text="super-secret private provider response")
        ),
    )
    try:
        with pytest.raises(LiteratureError, match="HTTP 403") as error:
            await bad.rerank("Which?", hits, lookup)
        assert "super-secret" not in str(error.value)
    finally:
        await bad.close()


def test_v2_schema_paths_and_selection_reject_cross_domain(tmp_path):
    with pytest.raises(LiteratureError):
        v2_path(tmp_path, tmp_path / ".local/medical_rehab_v1/report.json")
    assert v2_path(tmp_path, tmp_path / "eval/medical_rehab_v2/cases.json", public=True)
    with pytest.raises(ValueError):
        SuiteV2.model_validate(
            {
                "schema_version": 1,
                "domain": "metaverse_scenes",
                "experiment_id": "medical_rehab_v2",
                "cases": [],
            }
        )
    with pytest.raises(LiteratureError):
        validate_selection(
            {},
            {"domain": "metaverse_scenes"},
            type(
                "Gold", (), {"corpus_fingerprint": "c", "suite_fingerprint": "s", "test_seal": "t"}
            )(),
            "r",
            {},
        )


@pytest.mark.asyncio
async def test_v2_index_rejects_foreign_payload(tmp_path):
    client = AsyncQdrantClient(path=str(tmp_path / "qdrant"))
    collection = "rag_eval_medical_rehab_v2_test"
    try:
        await client.create_collection(
            collection, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE)
        )
        await client.upsert(
            collection,
            points=[
                models.PointStruct(
                    id=str(UUID(int=1)), vector=[1.0, 0.0], payload={"domain": "metaverse_scenes"}
                )
            ],
        )
        with pytest.raises(LiteratureError, match="Foreign"):
            await index_v2(client, collection, [], [], "c", "r", 2)
    finally:
        await client.close()


def test_holdout_is_new_and_gold_covers_full_comparisons():
    root = Path(__file__).resolve().parents[1]
    base = root / "eval/medical_rehab_v2"
    suite = SuiteV2.model_validate_json((base / "cases.json").read_text(encoding="utf8"))
    gold = GoldV2.model_validate_json((base / "gold.json").read_text(encoding="utf8"))
    assert {c.id for c in suite.cases if c.split == "test"} == {f"Q{n}" for n in range(41, 53)}
    gmap = {c.id: c for c in gold.cases}
    assert len(gmap["Q15"].required_groups) == 6
    assert len(gmap["Q18"].required_groups) == 4
    assert len(gmap["Q14"].required_groups) == 5
    for qid in ("Q51", "Q52"):
        assert not gmap[qid].scored and not gmap[qid].required_groups
    manifest = root / ".local/medical_rehab_v1/corpus/manifest.json"
    if manifest.exists():
        m, p, c = load_corpus(manifest, root / "doc")
        validate_gold(gold, suite, m, p, c)


def test_selection_prefers_complete_evidence_to_single_hit():
    from rag_portfolio.evaluation.medical_v2 import VARIANTS

    summary = {
        n: {"complete_evidence_at_5": 0.2, "required_evidence_coverage": 0.4, "mrr_at_5": 0.9}
        for n in VARIANTS
    }
    summary["source_routed"]["complete_evidence_at_5"] = 0.3
    assert select_variant(summary) == "source_routed"
