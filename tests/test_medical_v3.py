import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models

from rag_portfolio.config import Settings
from rag_portfolio.evaluation.medical_v3 import (
    CONFIG,
    ContextPacker,
    GoldV3,
    SuiteV3,
    coverage_order,
    group_hits,
    input_action,
    plan_query,
    round_robin_pool,
    validate_selection,
)
from rag_portfolio.evaluation.medical_v3_runner import RerankerV3, index_v3
from rag_portfolio.evaluation.retrievers import Candidate
from rag_portfolio.literature_backup import LiteratureError


def hit(number, score):
    return Candidate(UUID(int=number), score)


def test_planner_keeps_medical_topics_and_has_no_gold_input():
    aliases = {"D129": ["外骨骼综述"], "D161": ["机器人平衡综述"]}
    docs = {
        "D129": {"title": "Wearable exoskeleton after stroke"},
        "D161": {"title": "Robot gait training balance after stroke"},
    }
    sources, tasks = plan_query(
        "外骨骼综述与机器人平衡综述的人群、干预和结局有何差异？", aliases, docs
    )
    assert sources == ["D129", "D161"]
    assert {t.facet for t in tasks} == {"population", "intervention", "outcome"}
    assert len(tasks) == 6
    assert all("stroke" in t.query for t in tasks)
    assert set(inspect.signature(plan_query).parameters) == {"query", "aliases", "documents"}
    assert plan_query("不指定来源的训练结局问题", aliases, docs)[0] == []


def test_coverage_uses_subquery_ranks_and_preserves_real_scores():
    a, b, c = hit(1, 0.9), hit(2, 0.85), hit(3, 0.8)
    tasks = {"source:population": [hit(1, 1.0), hit(2, 0.9)], "source:outcome": [hit(3, 1.0)]}
    ordered, audit = coverage_order([a, b, c], tasks)
    assert ordered == [a, c, b]
    assert [h.score for h in ordered] == [0.9, 0.8, 0.85]
    assert len(audit) == 3
    assert set(inspect.signature(coverage_order).parameters) == {"ranked", "task_rankings"}


def test_union_evidence_requires_no_gaps_and_correct_source():
    ref = SimpleNamespace(document_id="D001", pdf_page=2, start_char=10, end_char=40)
    gold = SimpleNamespace(required_groups=[SimpleNamespace(alternatives=[ref])])
    cards = [
        {"document_id": "D001", "pdf_page_start": 2, "start_char": 5, "end_char": 25},
        {"document_id": "D001", "pdf_page_start": 2, "start_char": 25, "end_char": 45},
    ]
    assert group_hits(cards, gold) == [True]
    cards[1]["start_char"] = 26
    assert group_hits(cards, gold) == [False]
    cards[1]["start_char"] = 25
    cards[1]["document_id"] = "D003"
    assert group_hits(cards, gold) == [False]


def test_bounded_context_retains_units_negation_and_source_offsets(monkeypatch):
    pytest.importorskip("tiktoken")
    text = "Do not prescribe from this paragraph. Measured speed is not supplied. " * 40
    page = {"document_id": "D001", "pdf_page": 1, "text": text, "quality_flags": []}
    chunk = {
        "document_id": "D001",
        "title": "Source",
        "source_path": "selected/source.pdf",
        "source_sha256": "a" * 64,
        "pdf_page_start": 1,
        "start_char": 40,
        "end_char": 110,
    }
    packer = ContextPacker([page], {str(UUID(int=1)): chunk})
    card = packer.card(str(UUID(int=1)), expand=True)
    assert card["start_char"] <= 40 and card["end_char"] >= 110
    assert card["text"] == text[card["start_char"] : card["end_char"]]
    assert "not" in card["text"]
    monkeypatch.setitem(CONFIG, "context_budget", 60)
    pack = packer.pack([hit(1, 0.9)], expand=True)
    assert pack["reference_tokens_used"] <= 60
    assert pack["model_billing_tokens"] is None
    page["quality_flags"] = ["control_glyphs"]
    with pytest.raises(LiteratureError):
        packer.card(str(UUID(int=1)), expand=True)


def test_round_robin_limits_union_and_deduplicates_ids():
    a, b, c = hit(1, 1.0), hit(2, 0.9), hit(3, 0.8)
    assert round_robin_pool([[a, b], [a, c]], 3) == [a, b, c]
    assert round_robin_pool([[a, b], [c]], 2) == [a, c]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("某患者实际测得的步速是多少？", "request_patient_data"),
        ("请给出未提供病情患者的个体化治疗处方。", "request_patient_data"),
        ("医院下周门诊挂号时段是什么？", "needs_live_data"),
        ("本地收录了所有更新版指南吗？", "check_corpus_inventory"),
        ("这个设备维修密码是什么？", "unsupported_secret"),
        ("指南如何定义平衡？", "retrieve_literature"),
    ],
)
def test_input_requirements_do_not_depend_on_similarity_score(query, expected):
    assert input_action(query, {}) == expected


def test_new_holdout_and_full_reference_policy_are_sealed():
    root = Path(__file__).resolve().parents[1]
    base = root / "eval/medical_rehab_v3"
    suite = SuiteV3.model_validate_json((base / "cases.json").read_text(encoding="utf8"))
    gold = GoldV3.model_validate_json((base / "gold.json").read_text(encoding="utf8"))
    assert {c.id for c in suite.cases if c.split == "test"} == {f"Q{i}" for i in range(53, 73)}
    assert sum(c.scored for c in gold.cases if int(c.id[1:]) >= 53) == 16
    assert all(c.split == "dev" for c in suite.cases if int(c.id[1:]) <= 52)
    # Inspect exposed development data only; do not tune input rules on new holdout labels.
    aliases = json.loads((base / "source_aliases.json").read_text(encoding="utf8"))
    for case in suite.cases:
        if case.split == "dev":
            assert input_action(case.query, aliases) == case.expected_action, case.id
    old_aim = next(
        g for c in gold.cases if c.id == "Q17" for g in c.required_groups if g.id == "exo_aim"
    )
    assert len(old_aim.alternatives) == 3


def test_v3_rejects_cross_domain_and_changed_selection():
    with pytest.raises(ValueError):
        SuiteV3.model_validate(
            {
                "schema_version": 1,
                "domain": "metaverse_scenes",
                "experiment_id": "medical_rehab_v3",
                "cases": [],
            }
        )
    gold = SimpleNamespace(corpus_fingerprint="c", suite_fingerprint="s", test_seal="t")
    with pytest.raises(LiteratureError):
        validate_selection({}, {"experiment_id": "medical_rehab_v2"}, gold, "e", {})


@pytest.mark.asyncio
async def test_v3_rerank_namespace_and_redaction(tmp_path):
    settings = Settings(
        _env_file=None, embedding_base_url="https://example.test/api/v1", embedding_api_key="secret"
    )
    lookup = {str(UUID(int=1)): {"title": "PDF", "section_path": "", "text": "text"}}
    rerank = RerankerV3(
        settings,
        tmp_path / "cache.json",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "output": {"results": [{"index": 0, "relevance_score": 0.9}]},
                    "usage": {"total_tokens": 5},
                    "request_id": "request",
                },
            )
        ),
    )
    try:
        ranked, audit = await rerank.rerank("query", [hit(1, 0.1)], lookup)
        assert ranked[0].score == 0.9 and audit["request_id"] == "request"
        cache = json.loads((tmp_path / "cache.json").read_text())
        assert cache["experiment_id"] == "medical_rehab_v3"
    finally:
        await rerank.close()
    bad = RerankerV3(
        settings,
        tmp_path / "bad.json",
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret")),
    )
    try:
        with pytest.raises(LiteratureError) as error:
            await bad.rerank("query", [hit(1, 0.1)], lookup)
        assert "secret" not in str(error.value)
    finally:
        await bad.close()


@pytest.mark.asyncio
async def test_v3_index_rejects_v2_payload_even_in_medical_domain(tmp_path):
    client = AsyncQdrantClient(path=str(tmp_path / "qdrant"))
    name = "rag_eval_medical_rehab_v3_test"
    try:
        await client.create_collection(
            name, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE)
        )
        await client.upsert(
            name,
            points=[
                models.PointStruct(
                    id=str(UUID(int=1)),
                    vector=[1.0, 0.0],
                    payload={"domain": "medical_knowledge", "experiment_id": "medical_rehab_v2"},
                )
            ],
        )
        with pytest.raises(LiteratureError, match="Foreign"):
            await index_v3(client, name, [], [], "c", "e", 2)
    finally:
        await client.close()
