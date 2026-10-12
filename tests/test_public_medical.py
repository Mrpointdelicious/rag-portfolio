"""Mutation tests for rights, identities, source coordinates and Gold integrity."""

import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("tiktoken")

from rag_portfolio.public_medical import (  # noqa: E402
    article_xml,
    build_corpus,
    canonical,
    public_gate,
    sha,
    validate_corpus,
)
from rag_portfolio.public_medical_retrieval import (  # noqa: E402
    boundary,
    context_pack,
    coverage_order,
    validate_index,
)
from rag_portfolio.services.hybrid_contract import Snippet  # noqa: E402
from rag_portfolio.services.retrieval_algorithms import Candidate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def corpus():
    return json.loads(
        (ROOT / "artifacts/public-medical-corpus-v1/public-prose.json").read_text(encoding="utf8")
    )


def repin(c):
    c["corpus_sha256"] = sha(canonical({k: v for k, v in c.items() if k != "corpus_sha256"}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("license", "unknown"),
        ("rights_decision", "hold"),
        ("clinical_approval", True),
        ("authors", []),
        ("license_evidence", {}),
    ],
)
def test_rights_are_resource_and_scope_gated(corpus, field, value):
    doc = copy.deepcopy(corpus["documents"][0])
    doc[field] = value
    with pytest.raises(ValueError):
        public_gate(doc)


def test_pinned_public_corpus_and_evidence_roundtrip(corpus):
    validate_corpus(corpus)
    assert len(corpus["documents"]) == 7
    assert len({d["work_id"] for d in corpus["documents"]}) == 7
    assert all(c["page"] is None for c in corpus["chunks"])
    for c in corpus["chunks"]:
        Snippet.model_validate({k: c[k] for k in Snippet.model_fields})


@pytest.mark.parametrize(
    "field,value",
    [
        ("page", 1),
        ("work_id", "doi:wrong"),
        ("source_sha256", "0" * 64),
        ("locator", "/invented"),
        ("license", "unknown"),
        ("clinical_approval", True),
    ],
)
def test_coordinates_rejected_even_with_recomputed_payload_pin(corpus, field, value):
    corpus["chunks"][0][field] = value
    repin(corpus)
    with pytest.raises(ValueError):
        validate_corpus(corpus)


def test_jats_excludes_figures_and_peer_review(tmp_path):
    file = tmp_path / "a.xml"
    file.write_text(
        '<article><front><article-meta><article-id pub-id-type="doi">10.1/test</article-id>'
        "<title-group><article-title>Test</article-title></title-group><contrib-group>"
        "<contrib><name>A</name></contrib></contrib-group><permissions><license><license-p>"
        "https://creativecommons.org/licenses/by/4.0/</license-p></license></permissions>"
        "<abstract><p>Abstract.</p></abstract></article-meta></front><body><sec>"
        "<title>Methods</title><p>Allowed prose.</p><fig><caption><p>Third party.</p>"
        "</caption></fig></sec></body><sub-article><body><p>Reviewer confidential-style "
        "supplement.</p></body></sub-article></article>",
        encoding="utf8",
    )
    parsed = article_xml(file, "10.1/test")
    assert [p["text"] for p in parsed["paragraphs"]] == ["Abstract.", "Allowed prose."]
    assert all(p["page"] is None for p in parsed["paragraphs"])
    with pytest.raises(ValueError, match="DOI mismatch"):
        article_xml(file, "10.1/other")


def test_sealed_gold_is_source_grounded(corpus):
    folder = ROOT / "eval/public_medical_corpus_v1"
    seal = json.loads((folder / "seal.json").read_text(encoding="utf8"))
    segments = {s["segment_id"]: s for s in corpus["segments"]}
    for split, count in [("dev", 36), ("heldout", 14)]:
        raw = (folder / f"{split}.jsonl").read_bytes()
        assert sha(raw) == seal[split + "_sha256"]
        rows = [json.loads(line) for line in raw.splitlines()]
        assert len(rows) == count
        for row in rows:
            for group in row["evidence_groups"]:
                assert group["preferred"]
                for anchor in group["acceptable"]:
                    s = segments[anchor["segment_id"]]
                    assert s["text"][anchor["start"] : anchor["end"]] == anchor["text"]
                    assert sha(anchor["text"].encode()) == anchor["text_sha256"]


def test_index_drift_rejected_before_provider(corpus):
    with pytest.raises(ValueError, match="pin mismatch"):
        validate_index(
            corpus,
            {"corpus_sha256": "0" * 64, "corpus_id": corpus["corpus_id"], "index_sha256": "0" * 64},
        )


def test_context_budget_and_patient_boundary(corpus):
    from uuid import UUID

    top = [Candidate(UUID(c["chunk_id"]), 1) for c in corpus["chunks"][:5]]
    packed, tokens = context_pack(top, corpus, 4000, expanded=True)
    assert packed and tokens <= 4000
    assert boundary("患者没有实测FAC，请填写患者病历") == "needs_patient_data_and_clinical_review"
    assert boundary("公开库里的中国膝骨关节炎指南推荐是什么") == "out_of_public_scope"
    assert boundary("NEW05 平均985步是否规定处方") == "retrieve"


def test_pdf_physical_pages_and_separate_namespace(corpus):
    doc = copy.deepcopy(corpus["documents"][0])
    doc["format"] = "pdf"
    doc["source_sha256"] = sha(b"synthetic test PDF, no medical content")
    doc["segments"] = [
        {
            "text": "Synthetic test paragraph.",
            "page": 3,
            "section": "Methods",
            "locator": "pdf-physical-page:3",
            "coordinate_system": "normalized_pdf_page",
        }
    ]
    local = build_corpus("medical_combined_v1", [doc], public=False)
    assert local["chunks"][0]["page"] == 3
    assert local["chunks"][0]["chunk_id"] not in {c["chunk_id"] for c in corpus["chunks"]}


def test_coverage_balances_only_explicit_query_sources(corpus):
    from uuid import UUID

    first = [c for c in corpus["chunks"] if c["document_id"] == "NEW02"][:2]
    other = next(c for c in corpus["chunks"] if c["document_id"] == "NEW03")
    rows = {c["chunk_id"]: c for c in first + [other]}
    ranked = [Candidate(UUID(c["chunk_id"]), 1) for c in first + [other]]
    chosen = coverage_order("比较 NEW02 和 NEW03", ranked, rows)
    assert {rows[str(c.chunk_id)]["document_id"] for c in chosen[:2]} == {"NEW02", "NEW03"}


def test_service_scope_guards_and_signed_boundary(corpus, monkeypatch):
    import hashlib
    import hmac
    from types import SimpleNamespace
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from rag_portfolio import public_medical_service
    from rag_portfolio.services.hybrid_contract import RetrievalResponse

    # No provider call is allowed in these authorization/negative-path tests.
    monkeypatch.setattr(
        public_medical_service, "PublicMedicalRetriever", lambda c, i: SimpleNamespace()
    )
    token = "test-only-ephemeral-" + "x" * 32
    app = public_medical_service.create_medical_app(corpus, {"index_sha256": "1" * 64}, token)
    body = {
        "request_id": str(uuid4()),
        "query": "公开库里中国膝骨关节炎指南推荐等级",
        "domain": "health",
        "scope_key": "medical-public-v1",
        "corpus_version": corpus["release"],
        "corpus_sha256": corpus["corpus_sha256"],
        "index_sha256": "1" * 64,
    }
    headers = {
        "Authorization": "Bearer " + token,
        "X-Tenant-ID": "medical-public-v1",
        "X-Actor-Role": "operator",
        "X-Patient-Context": "false",
    }
    with TestClient(app) as client:
        path = "/v1/experimental/hybrid/retrieve"
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(
                path, json=body, headers={**headers, "X-Patient-Context": "true"}
            ).status_code
            == 403
        )
        assert (
            client.post(path, json={**body, "corpus_sha256": "0" * 64}, headers=headers).status_code
            == 409
        )
        response = client.post(path, json=body, headers=headers)
        value = response.json()
        parsed = RetrievalResponse.model_validate(value)
        assert parsed.selected_count == 0 and parsed.model_calls == {"embedding": 0, "rerank": 0}
        assert parsed.code == "out_of_public_scope"
        assert hmac.compare_digest(
            value["signature"],
            hmac.new(
                token.encode(),
                canonical({k: v for k, v in value.items() if k != "signature"}),
                hashlib.sha256,
            ).hexdigest(),
        )
