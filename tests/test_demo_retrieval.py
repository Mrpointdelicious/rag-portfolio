"""Isolated ASGI tests, with real BM25 and original demo documents (no archived rankings)."""

import hashlib
import hmac
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rag_portfolio.config import Settings
from rag_portfolio.main import create_app
from rag_portfolio.services.demo_retrieval import DemoResponse, canonical

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures/integration_demo/corpus.json"
TOKEN = "isolated-demo-test-token-" + "x" * 32
PIN = hashlib.sha256(CORPUS.read_bytes()).hexdigest()


def settings(**kw):
    return Settings(
        _env_file=None,
        environment="test",
        demo_retrieval_enabled=True,
        demo_token=TOKEN,
        demo_corpus_path=str(CORPUS),
        demo_corpus_sha256=PIN,
        **kw,
    )


def headers(**kw):
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-Tenant-ID": "integration-demo",
        "X-Actor-Role": "operator",
        "X-Patient-Context": "false",
        **kw,
    }


def query(**kw):
    return {
        "query": "六分钟步行距离",
        "scope_key": "integration-demo",
        "corpus_version": "demo-rehab-protocols-v1",
        **kw,
    }


URL = "/v1/experimental/demo/retrieve"


def test_real_query_schema_signature_and_exact_source_mapping():
    client = TestClient(create_app(settings()))
    rid = str(uuid4())
    response = client.post(URL, json=query(), headers=headers(**{"X-Request-ID": rid}))
    assert response.status_code == 200
    body = response.json()
    parsed = DemoResponse.model_validate(body)
    assert parsed.request_id == UUID(rid) and parsed.status == "ok"
    assert parsed.evidence[0].doc_ref == "protocol-a@v1#outcomes"
    expected = hmac.new(
        TOKEN.encode(),
        canonical({k: v for k, v in body.items() if k != "signature"}),
        hashlib.sha256,
    ).hexdigest()
    assert expected == body["signature"]
    docs = {d["code"]: d for d in json.loads(CORPUS.read_text(encoding="utf-8"))["documents"]}
    for hit in body["evidence"]:
        doc = docs[hit["doc_ref"].split("@")[0]]
        assert hit["text"] == doc["text"][hit["start"] : hit["end"]]
        assert hit["title"] == doc["title"] and hit["source"] == doc["source"]
        assert hit["page"] is None


@pytest.mark.parametrize(
    "change, status",
    [
        ({"Authorization": ""}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"X-Tenant-ID": "other"}, 403),
        ({"X-Actor-Role": "patient"}, 403),
        ({"X-Actor-Role": "clinician"}, 403),
        ({"X-Patient-Context": "true"}, 403),
    ],
)
def test_authentication_and_server_scope(change, status):
    client = TestClient(create_app(settings()))
    assert client.post(URL, json=query(), headers=headers(**change)).status_code == status


@pytest.mark.parametrize(
    "change,status",
    [
        ({"scope_key": "unapproved"}, 403),
        ({"corpus_version": "medical-v3"}, 409),
        ({"tenant_id": "other"}, 422),
        ({"approved": True}, 422),
        ({"top_k": 7}, 422),
        ({"top_k": True}, 422),
        ({"query": " "}, 422),
        ({"query": "x" * 4001}, 422),
        ({"max_chars": 6001}, 422),
    ],
)
def test_input_and_version_limits(change, status):
    client = TestClient(create_app(settings()))
    assert client.post(URL, json=query(**change), headers=headers()).status_code == status


def test_no_evidence_is_explicit_and_not_service_failure():
    client = TestClient(create_app(settings()))
    body = client.post(URL, json=query(query="zznonexistentquasar"), headers=headers()).json()
    assert body["status"] == "no_evidence" and body["evidence"] == []


def test_missing_index_and_pin_mismatch_are_unavailable(tmp_path):
    for s in [
        settings().model_copy(update={"demo_corpus_path": str(tmp_path / "missing")}),
        settings().model_copy(update={"demo_corpus_sha256": "0" * 64}),
    ]:
        response = TestClient(create_app(s)).post(URL, json=query(), headers=headers())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "demo_index_unavailable"


@pytest.mark.parametrize("mutation", ["unapproved", "span", "source", "hash"])
def test_corrupt_or_unapproved_source_is_rejected_even_with_new_pin(tmp_path, mutation):
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    doc = corpus["documents"][0]
    if mutation == "unapproved":
        doc["approved"] = False
    if mutation == "span":
        doc["sections"][0]["end"] = 99999
    if mutation == "source":
        doc["source"] = "repo://demo/other"
    if mutation == "hash":
        doc["text"] += "tampered"
    raw = json.dumps(corpus, ensure_ascii=False).encode()
    path = tmp_path / "corpus.json"
    path.write_bytes(raw)
    s = settings().model_copy(
        update={
            "demo_corpus_path": str(path),
            "demo_corpus_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    assert TestClient(create_app(s)).post(URL, json=query(), headers=headers()).status_code == 503


def test_atomic_budget_and_duplicate_suppression(tmp_path):
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    for doc in corpus["documents"]:
        doc["text"] = "康复训练" * 150 + "。"
        doc["text_sha256"] = hashlib.sha256(doc["text"].encode()).hexdigest()
        doc["sections"] = [
            dict(section_id=sid, title="预算测试", start=0, end=len(doc["text"]))
            for sid in ["one", "duplicate"]
        ]
    raw = json.dumps(corpus, ensure_ascii=False).encode()
    path = tmp_path / "corpus.json"
    path.write_bytes(raw)
    s = settings().model_copy(
        update={
            "demo_corpus_path": str(path),
            "demo_corpus_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    body = (
        TestClient(create_app(s))
        .post(URL, json=query(query="康复训练", max_chars=600), headers=headers())
        .json()
    )
    assert body["status"] == "no_evidence" and body["budget_truncated"]
    body = (
        TestClient(create_app(s))
        .post(URL, json=query(query="康复训练", max_chars=1202), headers=headers())
        .json()
    )
    assert len(body["evidence"]) == 2
    assert sum(len(s["text"]) for s in body["evidence"]) <= 1202


def test_disabled_and_production_and_invalid_configuration():
    client = TestClient(create_app(Settings(_env_file=None, environment="test")))
    assert client.post(URL, json=query(), headers=headers()).status_code == 404
    for update in [
        {"environment": "production"},
        {"demo_token": "short"},
        {"demo_corpus_path": ""},
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{**settings().model_dump(), **update})
    # The official release API remains explicitly unimplemented.
    client = TestClient(
        create_app(Settings(_env_file=None, environment="test", service_token=TOKEN))
    )
    response = client.post(
        "/v1/retrieve",
        json={"kb_ids": [str(uuid4())], "queries": [{"q": "x"}]},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 501
