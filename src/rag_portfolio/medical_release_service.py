"""Pinned local medical release server; authenticators never choose releases or scope."""

import asyncio
import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rag_portfolio.medical_release import ReleaseRegistry, combined_boundary, load
from rag_portfolio.public_medical import canonical
from rag_portfolio.public_medical_retrieval import PublicMedicalRetriever, boundary
from rag_portfolio.services.hybrid_contract import (
    RetrievalQuery,
    RetrievalResponse,
    Snippet,
    estimate_tokens,
)

TENANT = "medical-business-v1"


def create_release_app(registry_path, token, *, fault_file=None):
    if len(token) < 32:
        raise ValueError("Distinct ephemeral token required")
    registry = ReleaseRegistry(registry_path)
    clients = {}

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            for client in clients.values():
                await client.close()

    app = FastAPI(lifespan=lifespan)
    app.state.calls = []

    @app.get("/health/live")
    async def live():
        state = registry.read()
        return {
            "status": "up",
            "active": state["active"],
            "generation": state["generation"],
            "clinical_approval": False,
        }

    @app.post("/v1/experimental/hybrid/retrieve")
    async def retrieve(request: Request):
        if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            return JSONResponse({"code": "unauthorized"}, 401)
        if (
            request.headers.get("x-tenant-id") != TENANT
            or request.headers.get("x-actor-role") != "operator"
            or request.headers.get("x-patient-context") != "false"
        ):
            return JSONResponse({"code": "scope_denied"}, 403)
        if len(await request.body()) > 16384:
            return JSONResponse({"code": "request_too_large"}, 413)
        try:
            q = RetrievalQuery.model_validate(await request.json())
        except ValueError:
            return JSONResponse({"code": "invalid_request"}, 422)
        try:
            entry, (corpus, index, approval), generation = registry.active()
        except (ValueError, OSError, KeyError):
            return JSONResponse({"code": "release_unavailable"}, 409)
        if (
            q.scope_key != TENANT
            or q.corpus_version != corpus["release"]
            or q.corpus_sha256 != corpus["corpus_sha256"]
            or q.index_sha256 != index["index_sha256"]
        ):
            return JSONResponse({"code": "version_or_scope_unavailable"}, 409)
        route = boundary if corpus["corpus_id"] == "medical_public_v1" else combined_boundary
        gate = route(q.query) if q.domain == "health" else "out_of_domain_scope"
        start = time.perf_counter()
        call = {
            "request_id": str(q.request_id),
            "query": q.query,
            "domain": q.domain,
            "release_id": entry["release_id"],
            "generation": generation,
            "corpus_sha256": corpus["corpus_sha256"],
            "index_sha256": index["index_sha256"],
            "boundary": gate,
            "model_calls": {"embedding": 0, "rerank": 0},
        }
        app.state.calls.append(call)
        packed, actual = [], None
        fault = load(fault_file).get("mode", "none") if fault_file else "none"
        call["injected_fault"] = fault
        if fault == "provider_unavailable":
            call["code"] = "provider_unavailable"
            return JSONResponse({"code": "provider_unavailable"}, 503)
        if fault == "timeout":
            await asyncio.sleep(16)
            call["code"] = "injected_timeout"
            return JSONResponse({"code": "injected_timeout"}, 503)
        if gate == "retrieve" and fault != "empty":
            key = (entry["release_id"], entry["index_sha256"])
            try:
                if key not in clients:
                    clients[key] = PublicMedicalRetriever(
                        corpus, index, processing_approval=approval, route=route
                    )
                actual = await clients[key].retrieve(q.query)
            except (ValueError, RuntimeError):
                call["code"] = "provider_unavailable"
                return JSONResponse({"code": "provider_unavailable"}, 503)
            call["model_calls"] = actual["actual_model_calls"]
            strategy = "coverage" if q.strategy == "hybrid_selection" else q.strategy
            ranked = actual["strategies"][strategy]["top5"]
            for row in ranked[: q.top_k]:
                chunk = clients[key].chunks[row["chunk_id"]]
                snippet = Snippet.model_validate({k: chunk[k] for k in Snippet.model_fields})
                snippet = snippet.model_copy(update={"score": row["score"]})
                if (
                    sum(estimate_tokens(s.model_dump(mode="json")) for s in packed + [snippet])
                    <= q.context_budget
                ):
                    packed.append(snippet)
        current = registry.read()
        if current["active"] != entry["release_id"]:
            call["code"] = "release_changed_during_request"
            return JSONResponse({"code": "release_changed_during_request"}, 409)
        code = "ok" if packed else ("no_hits" if gate == "retrieve" else gate)
        count = actual["candidate_count"] if actual else 0
        reranked = bool(actual and q.strategy in {"hybrid_rerank", "hybrid_selection"})
        response = RetrievalResponse(
            request_id=q.request_id,
            retrieval_run_id=uuid4(),
            strategy=q.strategy,
            scope_key=q.scope_key,
            domain=q.domain,
            corpus_version=q.corpus_version,
            corpus_sha256=q.corpus_sha256,
            index_sha256=q.index_sha256,
            status="success" if packed else "unavailable",
            code=code,
            elapsed_ms=(time.perf_counter() - start) * 1000,
            reranker_used="qwen3.7-text-rerank" if reranked else None,
            rerank_applied=reranked,
            degradation=[],
            candidate_count=count,
            post_rerank_count=count,
            selected_count=len(packed),
            budget_truncated=bool(actual and len(packed) < min(5, q.top_k)),
            context_tokens_estimated=sum(
                estimate_tokens(s.model_dump(mode="json")) for s in packed
            ),
            model_calls=call["model_calls"],
            stages={
                "scope": "technical_evaluation_nonclinical",
                "boundary": gate,
                "release_id": entry["release_id"],
                "generation": generation,
                "public_prose_only": corpus["corpus_id"] == "medical_public_v1",
                "injected_fault": fault,
            },
            evidence=packed,
        )
        value = response.model_dump(mode="json")
        value["signature"] = hmac.new(
            token.encode(),
            canonical({k: v for k, v in value.items() if k != "signature"}),
            hashlib.sha256,
        ).hexdigest()
        call.update(
            code=code,
            selected_refs=[s.doc_ref for s in packed],
            elapsed_ms=response.elapsed_ms,
            provider_metadata={"embedding": actual["embedding"], "rerank": actual["rerank"]}
            if actual
            else {},
        )
        return value

    return app
