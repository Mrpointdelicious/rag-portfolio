from unittest.mock import AsyncMock
from uuid import uuid4

from rag_portfolio.api.dependencies import session


def test_agent_credential_is_read_only_and_uses_server_scope(client, monkeypatch):
    from pydantic import SecretStr

    from rag_portfolio.contracts import RetrieveResponse

    client.app.state.settings.agent_token = SecretStr("z" * 48)
    client.app.state.settings.agent_scope_keys = ["shared"]
    client.app.state.settings.agent_audience = "internal"
    captured = []

    async def run(database, vector, settings, principal, body, request_id):
        captured.append((principal.admin, principal.audience, principal.scope_keys))
        return RetrieveResponse(evidence=[], notes=[])

    monkeypatch.setattr("rag_portfolio.services.retrieval.retrieve", run)
    headers = {"Authorization": "Bearer " + "z" * 48}
    response = client.post(
        "/v1/retrieve", headers=headers, json={"kb_ids": [str(uuid4())], "queries": [{"q": "test"}]}
    )
    assert response.status_code == 200 and captured == [(False, "internal", ("shared",))]
    assert (
        client.post("/v1/knowledge-bases", headers=headers, json={"code": "rehab_kb"}).status_code
        == 403
    )


def test_liveness_and_request_id(client):
    response = client.get("/health/live", headers={"X-Request-ID": "not-a-uuid"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(response.headers["X-Request-ID"]) == 36


def test_retrieve_requires_authentication(client):
    response = client.post(
        "/v1/retrieve", json={"kb_ids": [str(uuid4())], "queries": [{"q": "test"}]}
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_retrieval_uses_main_service_with_trusted_principal(client, read_headers, monkeypatch):
    from rag_portfolio.contracts import RetrieveResponse

    captured = []

    async def run(database, vector, settings, principal, body, request_id):
        captured.append((principal.tenant_id, principal.audience, body.strategy))
        return RetrieveResponse(evidence=[], notes=["no_eligible_evidence"])

    monkeypatch.setattr("rag_portfolio.services.retrieval.retrieve", run)
    response = client.post(
        "/v1/retrieve",
        headers=read_headers,
        json={"kb_ids": [str(uuid4())], "queries": [{"q": "test"}]},
    )
    assert response.status_code == 200
    assert response.json()["evidence"] == []
    assert captured == [("test", "patient", "hybrid")]


def test_request_cannot_override_tenant_or_audience(client, read_headers):
    response = client.post(
        "/v1/retrieve",
        headers=read_headers,
        json={
            "kb_ids": [str(uuid4())],
            "queries": [{"q": "test"}],
            "tenant_id": "other",
            "audience": "internal",
        },
    )
    assert response.status_code == 422
    assert "other" not in response.text


def test_embedding_failure_returns_retryable_error_without_provider_details(
    client, read_headers, monkeypatch
):
    from rag_portfolio.adapters.embedding import EmbeddingAdapterError

    monkeypatch.setattr(
        "rag_portfolio.services.retrieval.retrieve",
        AsyncMock(side_effect=EmbeddingAdapterError("sensitive-provider-detail")),
    )
    response = client.post(
        "/v1/retrieve",
        headers=read_headers,
        json={"kb_ids": [str(uuid4())], "queries": [{"q": "test"}]},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"
    assert "sensitive-provider-detail" not in response.text


def test_read_token_cannot_create_knowledge_base(client, read_headers):
    response = client.post("/v1/knowledge-bases", headers=read_headers, json={"code": "rehab_kb"})
    assert response.status_code == 403


def test_scene_base_requires_registered_space(client, admin_headers):
    response = client.post("/v1/knowledge-bases", headers=admin_headers, json={"code": "scene_kb"})
    assert response.status_code == 422


def test_reader_query_is_bound_to_server_scope(client, read_headers):
    captured = []

    class Result:
        def all(self):
            return []

    class Session:
        async def scalars(self, statement):
            captured.append(statement.compile().params)
            return Result()

    async def fake_session():
        yield Session()

    client.app.dependency_overrides[session] = fake_session
    response = client.get("/v1/knowledge-bases", headers={**read_headers, "X-Tenant-ID": "other"})
    assert response.status_code == 200
    assert captured[0]["tenant_id_1"] == "test"
    assert captured[0]["scope_key_1"] == ["shared"]


def test_readiness_fails_on_dependency_failure_without_leaking_details(client):
    client.app.state.vector.check = AsyncMock(side_effect=RuntimeError("sensitive-connection-data"))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["qdrant"] == "unavailable"
    assert "sensitive" not in response.text
