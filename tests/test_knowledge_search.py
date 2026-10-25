"""Public contract, routing authorization, and evidence packing regressions."""

import hashlib
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from rag_portfolio.contracts import Evidence, RetrieveResponse
from rag_portfolio.services.documents import DocumentServiceError


def evidence(text="Published synthetic evidence.", **changes):
    return Evidence(
        document_id=uuid4(),
        version_id=uuid4(),
        chunk_id=uuid4(),
        release_id=uuid4(),
        title="Synthetic manual",
        section="Getting started",
        source_locator="manual.pdf",
        text=text,
        score=0.95,
        retrieved_at=datetime.now(UTC),
        page=12,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        **changes,
    )


@pytest.fixture
def configured(client, monkeypatch):
    settings = client.app.state.settings
    health, hospital, other = uuid4(), uuid4(), uuid4()
    settings.knowledge_global_kb_ids = [health]
    settings.knowledge_space_kb_ids = {
        "hospital-a": {"help": [hospital], "product": [hospital]},
        "hospital-b": {"help": [other]},
    }
    row = evidence()
    result = RetrieveResponse(
        evidence=[row],
        notes=[],
        trace_id=uuid4(),
        query_decisions=[{"covered": True, "accepted_chunk_ids": [str(row.chunk_id)]}],
    )
    kernel = AsyncMock(return_value=result)
    monkeypatch.setattr("rag_portfolio.services.knowledge_search.retrieve", kernel)
    return kernel, (health, hospital, other)


def test_search_is_authenticated_and_scope_is_not_a_credential(client, read_headers, configured):
    kernel, _ = configured
    payload = {"query": "How to start?", "domain": "help", "scope": {"space_id": "hospital-a"}}
    assert client.post("/v1/knowledge/search", json=payload).status_code == 401
    kernel.side_effect = DocumentServiceError("knowledge_base_not_allowed", 403)
    result = client.post("/v1/knowledge/search", headers=read_headers, json=payload)
    assert result.status_code == 403
    assert result.json()["error"] == {"code": "knowledge_base_not_allowed", "retryable": False}
    principal = kernel.call_args.args[3]
    assert principal.tenant_id == "test" and principal.scope_keys == ("shared",)
    assert principal.admin is False


@pytest.mark.parametrize(
    "domain,space,index",
    [
        ("health", None, 0),
        ("health", "unknown", 0),
        ("help", "hospital-a", 1),
        ("product", "hospital-a", 1),
        ("help", "hospital-b", 2),
    ],
)
def test_business_route_and_policy_are_owned_by_service(
    client, read_headers, configured, domain, space, index
):
    kernel, ids = configured
    response = client.post(
        "/v1/knowledge/search",
        headers=read_headers,
        json={
            "query": "How to start?",
            "domain": domain,
            "scope": {"space_id": space},
            "limits": {"max_context_tokens": 16000},
        },
    )
    assert response.status_code == 200
    request = kernel.call_args.args[4]
    assert request.kb_ids == [ids[index]]
    assert request.max_context_tokens == 4000 and request.rerank
    assert request.strategy == "hybrid" and request.answer_mode == "evidence"
    body = response.json()
    assert body["status"] == "ok" and "[S1]" in body["context"]
    assert "第 12 页" in body["sources"][0]["locator"]
    assert str(kernel.return_value.evidence[0].version_id) in body["sources"][0]["ref"]
    assert not {"kb_ids", "scores", "notes", "policy_version", "query_decisions"} & body.keys()


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"query": "q", "domain": "help"}, 422),
        ({"query": "q", "domain": "product", "scope": {"space_id": "unknown"}}, 403),
        ({"query": "q", "kb_ids": [str(uuid4())]}, 422),
        ({"query": "q", "rerank": False}, 422),
        ({"query": "q", "scope": {"space_id": "hospital-a", "tenant_id": "other"}}, 422),
        ({"query": " "}, 422),
    ],
)
def test_invalid_or_unmapped_requests_do_not_reach_retrieval(
    client, read_headers, configured, payload, status
):
    kernel, _ = configured
    response = client.post("/v1/knowledge/search", headers=read_headers, json=payload)
    assert response.status_code == status
    kernel.assert_not_awaited()


@pytest.mark.parametrize("mode,status", [("empty", 200), ("unavailable", 503)])
def test_no_evidence_and_provider_failure_are_distinct(
    client, read_headers, configured, mode, status
):
    kernel, _ = configured
    kernel.return_value = RetrieveResponse(
        evidence=[],
        notes=["no_eligible_evidence"],
        trace_id=uuid4(),
        decision="unavailable" if mode == "unavailable" else "reject",
    )
    response = client.post("/v1/knowledge/search", headers=read_headers, json={"query": "q"})
    assert response.status_code == status
    if status == 200:
        assert response.json()["status"] == "no_evidence"
        assert response.json()["context"] == "" and response.json()["sources"] == []
    else:
        assert response.json()["error"]["retryable"]


def test_partial_evidence_and_label_budget_are_preserved(
    client, read_headers, configured, monkeypatch
):
    kernel, _ = configured
    row = kernel.return_value.evidence[0]
    kernel.return_value.evidence.append(evidence("Long additional excerpt " * 40))
    kernel.return_value.query_decisions.append({"covered": False})
    # A deterministic token counter isolates packing behavior from external tokenizer assets.
    monkeypatch.setattr("rag_portfolio.services.knowledge_search.token_ids", list)
    response = client.post(
        "/v1/knowledge/search",
        headers=read_headers,
        json={
            "query": "q",
            "limits": {"max_context_tokens": 128},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial" and body["gaps"]
    assert len(body["context"]) <= 128 and len(body["sources"]) == 1
    assert str(row.chunk_id) in body["sources"][0]["ref"]


def test_shipped_openapi_is_generated_from_the_live_public_route():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "export_contract", root / "tools/export_knowledge_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shipped = json.loads((root / "contracts/knowledge-search-v1.openapi.json").read_text("utf-8"))
    assert shipped == module.contract()


def test_facade_cannot_bypass_real_kernel_tenant_and_scope_checks(
    client, read_headers, configured, monkeypatch
):
    from rag_portfolio.services.retrieval import retrieve

    statements = []

    class DeniedSession:
        async def scalars(self, statement):
            statements.append(statement.compile().params)
            return []

    @asynccontextmanager
    async def sessions():
        yield DeniedSession()

    monkeypatch.setattr(client.app.state.database, "sessions", sessions)
    monkeypatch.setattr("rag_portfolio.services.knowledge_search.retrieve", retrieve)
    result = client.post(
        "/v1/knowledge/search",
        headers=read_headers,
        json={
            "query": "q",
            "domain": "help",
            "scope": {"space_id": "hospital-a"},
        },
    )
    assert result.status_code == 403
    assert statements[0]["tenant_id_1"] == "test"
    assert statements[0]["scope_key_1"] == ["shared"]
    assert statements[0]["id_1"] == [configured[1][1]]


def test_unconfigured_public_route_fails_without_retrieval(client, read_headers, monkeypatch):
    kernel = AsyncMock()
    monkeypatch.setattr("rag_portfolio.services.knowledge_search.retrieve", kernel)
    result = client.post("/v1/knowledge/search", headers=read_headers, json={"query": "q"})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "knowledge_not_configured"
    kernel.assert_not_awaited()
