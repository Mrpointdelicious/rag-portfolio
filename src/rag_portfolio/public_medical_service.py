"""Opt-in loopback public-medical test service; existing MetaAgent contract unchanged."""

import hashlib
import hmac
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rag_portfolio.public_medical import canonical
from rag_portfolio.public_medical_retrieval import PublicMedicalRetriever, boundary
from rag_portfolio.services.hybrid_contract import (
    RetrievalQuery,
    RetrievalResponse,
    Snippet,
    estimate_tokens,
)


def create_medical_app(corpus, index, token):
    if len(token) < 32:
        raise ValueError("Distinct ephemeral token required")
    retriever = PublicMedicalRetriever(corpus, index)
    app = FastAPI()
    app.state.retriever = retriever
    app.state.calls = []

    @app.get("/health/live")
    async def live():
        return {"status": "up", "clinical_approval": False}

    @app.post("/v1/experimental/hybrid/retrieve")
    async def retrieve(request: Request):
        if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            return JSONResponse({"code": "unauthorized"}, 401)
        if (
            request.headers.get("x-tenant-id") != "medical-public-v1"
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
        if (
            q.scope_key != "medical-public-v1"
            or q.domain != "health"
            or q.corpus_version != corpus["release"]
            or q.corpus_sha256 != corpus["corpus_sha256"]
            or q.index_sha256 != index["index_sha256"]
        ):
            return JSONResponse({"code": "version_or_scope_unavailable"}, 409)
        start = time.perf_counter()
        gate = boundary(q.query)
        packed, actual, count, reranked = [], None, 0, False
        if gate == "retrieve":
            try:
                actual = await retriever.retrieve(q.query)
            except (ValueError, RuntimeError):
                return JSONResponse({"code": "provider_unavailable"}, 503)
            strategy = "coverage" if q.strategy == "hybrid_selection" else q.strategy
            ranked = actual["strategies"][strategy]["top5"]
            count = actual["candidate_count"]
            reranked = q.strategy in {"hybrid_rerank", "hybrid_selection"}
            for row in ranked[: q.top_k]:
                chunk = retriever.chunks[row["chunk_id"]]
                snippet = Snippet.model_validate(
                    {k: chunk[k] for k in Snippet.model_fields}
                ).model_copy(update={"score": row["score"]})
                if (
                    sum(estimate_tokens(s.model_dump(mode="json")) for s in packed + [snippet])
                    <= q.context_budget
                ):
                    packed.append(snippet)
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
            code="ok" if packed else gate,
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
            model_calls=actual["actual_model_calls"] if actual else {"embedding": 0, "rerank": 0},
            stages={
                "scope": "technical_evaluation_nonclinical",
                "boundary": gate,
                "public_prose_only": True,
            },
            evidence=packed,
        )
        value = response.model_dump(mode="json")
        value["signature"] = hmac.new(
            token.encode(),
            canonical({k: v for k, v in value.items() if k != "signature"}),
            hashlib.sha256,
        ).hexdigest()
        app.state.calls.append(
            {
                "query": q.query,
                "request_id": str(q.request_id),
                "selected_count": len(packed),
                "model_calls": value["model_calls"],
                "boundary": gate,
            }
        )
        return value

    return app
