"""Dedicated technical-demo authority, independent from patient/admin tokens."""

import hashlib
import hmac
import logging
import time
from secrets import compare_digest
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from rag_portfolio.api.auth import Principal, bearer
from rag_portfolio.services.demo_retrieval import DemoIndex, DemoQuery, DemoResponse, canonical

router = APIRouter(prefix="/v1/experimental/demo", tags=["Isolated technical demo"])
logger = logging.getLogger(__name__)


async def demo_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    settings = request.app.state.settings
    if not settings.demo_retrieval_enabled or settings.environment == "production":
        raise HTTPException(404, "demo_disabled")
    token = settings.demo_token.get_secret_value()
    if not credentials or not token or not compare_digest(credentials.credentials, token):
        raise HTTPException(401, "unauthorized")
    if (
        request.headers.get("X-Tenant-ID") != settings.demo_tenant_id
        or request.headers.get("X-Actor-Role") != "operator"
        or request.headers.get("X-Patient-Context") != "false"
    ):
        raise HTTPException(403, "demo_scope_denied")
    return Principal(
        settings.demo_tenant_id,
        "demo-service",
        False,
        (settings.demo_scope_key,),
        audience="technical_demo",
    )


@router.post("/retrieve", response_model=DemoResponse)
async def retrieve_demo(
    body: DemoQuery, request: Request, principal: Annotated[Principal, Depends(demo_principal)]
):
    started = time.monotonic()
    settings = request.app.state.settings
    if body.scope_key not in principal.scope_keys:
        raise HTTPException(403, "demo_scope_denied")
    # Initialized only after authentication; never opens private assets or a production DB.
    index = getattr(request.app.state, "demo_index", None)
    if index is None:
        try:
            index = DemoIndex(settings)
        except (OSError, ValueError):
            logger.warning("demo_index_unavailable request=%s", request.state.request_id)
            raise HTTPException(503, "demo_index_unavailable") from None
        request.app.state.demo_index = index
    if body.corpus_version != index.corpus.corpus_version:
        raise HTTPException(409, "demo_version_unavailable")
    evidence, omitted = await index.search(body)
    result = DemoResponse(
        request_id=request.state.request_id,
        retrieval_run_id=uuid4(),
        corpus_version=index.corpus.corpus_version,
        corpus_sha256=index.sha256,
        status="ok" if evidence else "no_evidence",
        evidence=evidence,
        budget_truncated=omitted,
        elapsed_ms=(time.monotonic() - started) * 1000,
    )
    result.signature = hmac.new(
        settings.demo_token.get_secret_value().encode(),
        canonical(result.model_dump(mode="json", exclude={"signature"})),
        hashlib.sha256,
    ).hexdigest()
    logger.info(
        "demo_retrieve request=%s run=%s status=%s hits=%s elapsed_ms=%.1f",
        result.request_id,
        result.retrieval_run_id,
        result.status,
        len(result.evidence),
        result.elapsed_ms,
    )
    return result
