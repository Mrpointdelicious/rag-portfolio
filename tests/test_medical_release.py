"""Fail-closed version transitions and scopes, tested without provider credentials."""

import copy
import hashlib
import hmac
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytest.importorskip("tiktoken")

from rag_portfolio.medical_release import ReleaseRegistry, processing_gate  # noqa: E402
from rag_portfolio.public_medical import build_corpus, canonical, sha, write_json  # noqa: E402
from rag_portfolio.services.hybrid_contract import RetrievalResponse  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_new_business_questions_are_sealed_and_have_no_internal_source_ids():
    import re
    from collections import Counter

    directory = ROOT / "eval/medical_business_v1"
    seal = json.loads((directory / "seal.json").read_text(encoding="utf8"))
    all_cases = []
    for split, count in [("dev", 24), ("heldout", 16)]:
        raw = (directory / f"{split}.jsonl").read_bytes()
        assert sha(raw) == seal[f"{split}_cases_sha256"]
        cases = [json.loads(line) for line in raw.splitlines()]
        assert len(cases) == count
        for case in cases:
            for turn in case["turns"]:
                assert "evidence_groups" not in turn and "anchors" not in turn
                assert not re.search(r"\b(?:NEW0[1-6]|D\d{3})\b", turn["question"])
        all_cases.extend(cases)
    assert len({c["case_id"] for c in all_cases}) == 40
    assert set(Counter(c["category"] for c in all_cases).values()) == {5}


def test_all_twelve_backlog_cases_retain_original_questions_and_baselines():
    manifest = json.loads(
        (ROOT / "eval/public_medical_corpus_v1/regression_backlog.json").read_text(encoding="utf8")
    )
    baseline = manifest["baseline"]
    data = (ROOT / baseline["failure_cases_path"]).read_bytes()
    assert sha(data) == baseline["failure_cases_file_sha256"]
    original = {c["case_id"]: c for c in json.loads(data)["retrieval_failures"]}
    archived = {c["case_id"]: c for c in manifest["cases"]}
    assert archived.keys() == original.keys() and len(archived) == 12
    for case_id, case in archived.items():
        assert case["question"] == original[case_id]["question"]
        assert case["baseline"]["groups_total"] == original[case_id]["groups_total"]
        assert case["baseline"]["groups_covered_at5"] == original[case_id]["groups_covered_at5"]


def approval(corpus):
    value = {
        "schema_version": "medical-processing-approval-v1",
        "authority": "human_user_current_task",
        "corpus_sha256": corpus["corpus_sha256"],
        "external_model_processing": True,
        "public_redistribution": False,
        "clinical_approval": False,
        "documents": [
            {"document_id": d["document_id"], "source_sha256": d["source_sha256"]}
            for d in corpus["documents"]
        ],
    }
    value["approval_sha256"] = sha(canonical(value))
    return value


@pytest.fixture
def bundles(tmp_path):
    document = {
        "document_id": "TEST01",
        "work_id": "doi:10.1/synthetic",
        "doi": "10.1/synthetic",
        "title": "Synthetic source",
        "authors": ["Test author"],
        "source_url": "https://doi.org/10.1/synthetic",
        "source_sha256": sha(b"synthetic"),
        "format": "xml",
        "license": "CC-BY-4.0",
        "rights_decision": "approved_prose_only",
        "license_evidence": {"test": True},
        "approved_scope": "technical_evaluation_nonclinical",
        "clinical_approval": False,
        "segments": [
            {
                "text": "Synthetic source prose for version tests. No clinical instruction.",
                "page": None,
                "section": "Test",
                "locator": "/test/p[1]",
                "coordinate_system": "normalized_jats_paragraph",
            }
        ],
    }
    values = {}
    for name, public in [("public", True), ("combined", False)]:
        corpus = build_corpus(
            "medical_public_v1" if public else "medical_combined_v1", [document], public=public
        )
        index = {
            "corpus_id": corpus["corpus_id"],
            "corpus_sha256": corpus["corpus_sha256"],
            "dimension": 2,
            "model": "test",
            "rows": [
                {"chunk_id": c["chunk_id"], "text_sha256": c["text_sha256"], "vector": [1.0, 0.0]}
                for c in corpus["chunks"]
            ],
        }
        index["index_sha256"] = sha(canonical(index))
        cp, ip, ap = [tmp_path / f"{name}-{s}.json" for s in ["corpus", "index", "approval"]]
        write_json(cp, corpus)
        write_json(ip, index)
        write_json(ap, approval(corpus))
        values[name] = (cp, ip, ap, corpus, index)
    return values


@pytest.mark.parametrize("mutation", ["missing", "redistribution", "member", "corpus", "hash"])
def test_processing_confirmation_is_pinned_and_not_public_rights(bundles, mutation):
    corpus = bundles["combined"][3]
    value = approval(corpus)
    if mutation == "missing":
        value = None
    elif mutation == "redistribution":
        value["public_redistribution"] = True
    elif mutation == "member":
        value["documents"] = []
    elif mutation == "corpus":
        value["corpus_sha256"] = "0" * 64
    else:
        value["approval_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        processing_gate(corpus, value)


def setup_registry(tmp_path, bundles):
    registry = ReleaseRegistry(tmp_path / "registry.json")
    for name in ["public", "combined"]:
        cp, ip, ap, *_ = bundles[name]
        registry.register(name, cp, ip, ap if name == "combined" else None)
    return registry


def test_candidate_validation_revoke_and_explicit_rollback(tmp_path, bundles):
    registry = setup_registry(tmp_path, bundles)
    with pytest.raises(ValueError):
        registry.transition("combined", "activate")
    registry.transition("public", "validate")
    registry.transition("public", "activate")
    registry.transition("combined", "validate")
    registry.transition("combined", "activate")
    registry.transition("combined", "revoke")
    with pytest.raises(ValueError):
        registry.active()
    with pytest.raises(ValueError):
        registry.transition("combined", "activate")
    registry.transition("public", "rollback")
    assert registry.active()[0]["release_id"] == "public"
    assert registry.read()["history"][-1]["action"] == "rollback"
    with pytest.raises(ValueError):
        registry.register("public", *bundles["public"][:2])


def test_files_cannot_drift_after_registration(tmp_path, bundles):
    registry = setup_registry(tmp_path, bundles)
    cp = bundles["combined"][0]
    value = json.loads(cp.read_text(encoding="utf8"))
    value["release"] = "different-version"
    value["corpus_sha256"] = sha(
        canonical({k: v for k, v in value.items() if k != "corpus_sha256"})
    )
    write_json(cp, value)
    with pytest.raises(ValueError):
        registry.transition("combined", "validate")


def test_stale_writer_rejected_and_lock_cleaned(tmp_path, bundles):
    registry = setup_registry(tmp_path, bundles)
    stale = copy.deepcopy(registry.read())
    registry.transition("public", "validate")
    with pytest.raises(ValueError, match="Concurrent"):
        registry._save(stale, "validate", "combined")
    assert not registry.path.with_suffix(".lock").exists()
    registry.transition("combined", "validate")


def fake_retriever(monkeypatch, *, during=None):
    from rag_portfolio import medical_release_service

    class Fake:
        def __init__(self, corpus, index, **kwargs):
            self.corpus = corpus
            self.chunks = {c["chunk_id"]: c for c in corpus["chunks"]}

        async def retrieve(self, query):
            if during:
                during()
            return {
                "strategies": {
                    s: {"top5": [{"chunk_id": next(iter(self.chunks)), "score": 1.0}]}
                    for s in ["bm25", "dense", "hybrid", "hybrid_rerank", "coverage"]
                },
                "candidate_count": 1,
                "actual_model_calls": {"embedding": 1, "rerank": 1},
                "embedding": {},
                "rerank": {},
            }

        async def close(self):
            pass

    monkeypatch.setattr(medical_release_service, "PublicMedicalRetriever", Fake)


@pytest.mark.asyncio
async def test_readiness_stats_and_generation_change_fail_closed(tmp_path, bundles, monkeypatch):
    from rag_portfolio.medical_release_service import create_release_app

    registry = setup_registry(tmp_path, bundles)
    registry.transition("combined", "validate")
    registry.transition("combined", "activate")

    # A revoke then rollback to the same active ID must discard an in-flight result too.
    def change_generation():
        state = registry.read()
        registry._save(state, "audit_generation_change", "combined")

    fake_retriever(monkeypatch, during=change_generation)
    token = "t" * 40
    app = create_release_app(registry.path, token, trace_limit=1)
    c, i = bundles["combined"][3:]
    headers = {
        "Authorization": "Bearer " + token,
        "X-Tenant-ID": "medical-business-v1",
        "X-Actor-Role": "operator",
        "X-Patient-Context": "false",
    }
    body = dict(
        request_id=str(uuid4()),
        query="Synthetic source prose",
        domain="health",
        scope_key="medical-business-v1",
        corpus_version=c["release"],
        corpus_sha256=c["corpus_sha256"],
        index_sha256=i["index_sha256"],
        strategy="hybrid_rerank",
        context_budget=12000,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        assert (await client.get("/health/ready")).json()["corpus_sha256"] == c["corpus_sha256"]
        assert (await client.get("/internal/stats")).status_code == 401
        assert (
            await client.post("/v1/experimental/hybrid/retrieve", json=body, headers=headers)
        ).status_code == 409
        stats = (await client.get("/internal/stats", headers=headers)).json()
        assert stats["active"] == 0 and stats["http_statuses"] == {"409": 1}
        registry.transition("combined", "revoke")
        assert (await client.get("/health/ready")).status_code == 503


@pytest.mark.asyncio
async def test_service_pins_scope_signature_and_rollback(tmp_path, bundles, monkeypatch):
    from rag_portfolio.medical_release_service import create_release_app

    registry = setup_registry(tmp_path, bundles)
    for name in ["public", "combined"]:
        registry.transition(name, "validate")
        registry.transition(name, "activate")
    fake_retriever(monkeypatch)
    token = "owned-test-token-" + "x" * 40
    app = create_release_app(registry.path, token)
    headers = {
        "Authorization": "Bearer " + token,
        "X-Tenant-ID": "medical-business-v1",
        "X-Actor-Role": "operator",
        "X-Patient-Context": "false",
    }

    def body(name):
        c, i = bundles[name][3:]
        return {
            "request_id": str(uuid4()),
            "query": "Synthetic source prose",
            "domain": "health",
            "scope_key": "medical-business-v1",
            "corpus_version": c["release"],
            "corpus_sha256": c["corpus_sha256"],
            "index_sha256": i["index_sha256"],
            "strategy": "hybrid_rerank",
            "context_budget": 12000,
        }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        path = "/v1/experimental/hybrid/retrieve"
        assert (await client.post(path, json=body("combined"))).status_code == 401
        assert (
            await client.post(
                path, json=body("combined"), headers={**headers, "X-Patient-Context": "true"}
            )
        ).status_code == 403
        assert (await client.post(path, json=body("public"), headers=headers)).status_code == 409
        response = await client.post(path, json=body("combined"), headers=headers)
        value = response.json()
        parsed = RetrievalResponse.model_validate(value)
        assert parsed.selected_count == 1
        assert hmac.compare_digest(
            value["signature"],
            hmac.new(
                token.encode(),
                canonical({k: v for k, v in value.items() if k != "signature"}),
                hashlib.sha256,
            ).hexdigest(),
        )
        combined_ref = parsed.evidence[0].doc_ref
        registry.transition("combined", "revoke")
        assert (await client.post(path, json=body("combined"), headers=headers)).status_code == 409
        registry.transition("public", "rollback")
        old = await client.post(path, json=body("public"), headers=headers)
        assert old.status_code == 200 and old.json()["evidence"][0]["doc_ref"] != combined_ref
        assert (await client.post(path, json=body("combined"), headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_inflight_revocation_discards_result(tmp_path, bundles, monkeypatch):
    from rag_portfolio.medical_release_service import create_release_app

    registry = setup_registry(tmp_path, bundles)
    registry.transition("combined", "validate")
    registry.transition("combined", "activate")
    fake_retriever(monkeypatch, during=lambda: registry.transition("combined", "revoke"))
    app = create_release_app(registry.path, "x" * 40)
    c, i = bundles["combined"][3:]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        response = await client.post(
            "/v1/experimental/hybrid/retrieve",
            json={
                "request_id": str(uuid4()),
                "query": "source prose",
                "domain": "health",
                "scope_key": "medical-business-v1",
                "corpus_version": c["release"],
                "corpus_sha256": c["corpus_sha256"],
                "index_sha256": i["index_sha256"],
            },
            headers={
                "Authorization": "Bearer " + "x" * 40,
                "X-Tenant-ID": "medical-business-v1",
                "X-Actor-Role": "operator",
                "X-Patient-Context": "false",
            },
        )
    assert response.status_code == 409
    assert response.json()["code"] == "release_changed_during_request"
