"""Dedicated synthetic-patient contract; never impersonates an operator/nonpatient request."""

import hashlib
import hmac
import json
import time
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

CONCEPTS = {
    "robotic_gait": "robot assisted gait rehabilitation after stroke planned outcome measures",
    "balance": "robot assisted gait stroke balance systematic review evidence limitations",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class ConceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    concept: Literal["robotic_gait", "balance"]
    patient_context: Literal["synthetic"]


def create_preproduction_app(*, authorize, release_state, retrieve, response_key):
    """Injected authority must validate the knowledge audience and current patient grant."""
    if len(response_key) < 32:
        raise ValueError("Dedicated owned response key required")
    app = FastAPI()
    app.state.outbound = []
    app.state.response_transform = lambda value: value

    async def subject(request):
        return await authorize(request.headers.get("authorization", ""))

    @app.get("/v1/preproduction/release")
    async def release(request: Request):
        await subject(request)
        return release_state()

    @app.post("/v1/preproduction/retrieve")
    async def query(body: ConceptRequest, request: Request):
        claims = await subject(request)
        before = dict(release_state())
        if not before["active"]:
            return JSONResponse({"code": "release_unavailable"}, 409)
        text = CONCEPTS[body.concept]
        app.state.outbound.append(
            {
                "concept": body.concept,
                "query_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "patient_fields": False,
            }
        )
        snippets = await retrieve(text)
        if release_state() != before:
            return JSONResponse({"code": "release_changed"}, 409)
        # An authorization revoked while the provider was running blocks this response.
        await subject(request)
        scope = [
            claims[k]
            for k in [
                "tenant",
                "actor",
                "patient",
                "role",
                "purpose",
                "session",
                "grant_generation",
            ]
        ]
        value = dict(
            schema_version="synthetic-knowledge-v1",
            patient_context="synthetic",
            scope_hash=hashlib.sha256(canonical(scope)).hexdigest(),
            release=before,
            expires_at=int(time.time()) + 60,
            source_kind="literature",
            snippets=[
                dict(
                    doc_ref=s["doc_ref"],
                    text=s["text"],
                    text_sha256=hashlib.sha256(s["text"].encode()).hexdigest(),
                )
                for s in snippets
            ],
        )
        value["signature"] = hmac.new(response_key.encode(), canonical(value), "sha256").hexdigest()
        return app.state.response_transform(value)

    return app
