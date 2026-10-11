"""Standalone opt-in HTTP service; never constructs database or Qdrant clients."""

import hashlib
import hmac
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from rag_portfolio.services.hybrid_contract import RetrievalQuery, RetrievalResponse, canonical
from rag_portfolio.services.hybrid_retrieval import HybridPipeline


def create_app(config, provider, *, pipeline=None):
    @asynccontextmanager
    async def lifespan(app):
        if config.enabled:
            app.state.pipeline = pipeline or HybridPipeline(config, provider)
        try:
            yield
        finally:
            if config.enabled:
                await app.state.pipeline.close()

    app = FastAPI(title="Isolated Hybrid Retrieval", lifespan=lifespan)
    app.state.config = config

    @app.exception_handler(HTTPException)
    async def failure(request, exc):
        return JSONResponse({"code": exc.detail}, status_code=exc.status_code)

    @app.get("/health/live")
    async def health():
        if not config.enabled or not hasattr(app.state, "pipeline"):
            raise HTTPException(503, "hybrid_disabled")
        try:
            app.state.pipeline.check_pins()
        except (OSError, ValueError):
            raise HTTPException(503, "index_drift") from None
        return {
            "status": "ok",
            "corpus_sha256": config.corpus_sha256,
            "index_sha256": config.index_sha256,
        }

    @app.middleware("http")
    async def limits(request: Request, call_next):
        if request.method == "POST":
            # Size is bounded before JSON parsing, including chunked request bodies.
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 20000:
                    return JSONResponse({"code": "request_too_large"}, status_code=413)
            request._body = bytes(raw)
        return await call_next(request)

    @app.post("/v1/experimental/hybrid/retrieve", response_model=RetrievalResponse)
    async def retrieve(body: RetrievalQuery, request: Request):
        if not config.enabled or config.environment == "production":
            raise HTTPException(404, "hybrid_disabled")
        if not hmac.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + config.token.get_secret_value()
        ):
            raise HTTPException(401, "unauthorized")
        if (
            request.headers.get("X-Tenant-ID") != config.tenant_id
            or request.headers.get("X-Actor-Role") != "operator"
            or request.headers.get("X-Patient-Context") != "false"
            or body.scope_key != config.scope_key
            or body.domain not in config.domains
        ):
            raise HTTPException(403, "scope_denied")
        try:
            if UUID(request.headers.get("X-Request-ID", "")) != body.request_id:
                raise ValueError("request mismatch")
        except ValueError:
            raise HTTPException(422, "request_id_mismatch") from None
        try:
            result = await app.state.pipeline.search(body)
        except TimeoutError:
            raise HTTPException(503, "retrieval_timeout") from None
        except (OSError, ValueError) as exc:
            code = str(exc)
            if code == "version_unavailable":
                raise HTTPException(409, code) from None
            raise HTTPException(503, "index_or_budget_unavailable") from None
        result.signature = hmac.new(
            config.token.get_secret_value().encode(),
            canonical(result.model_dump(mode="json", exclude={"signature"})),
            hashlib.sha256,
        ).hexdigest()
        if len(canonical(result.model_dump(mode="json"))) > 65536:
            raise HTTPException(503, "response_budget_exceeded")
        return result

    return app
