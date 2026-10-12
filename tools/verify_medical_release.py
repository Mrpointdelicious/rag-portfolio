"""Actual provider-backed release/rollback probes against the owned ASGI service."""

import argparse
import asyncio
import hashlib
import hmac
import secrets
from pathlib import Path
from uuid import uuid4

import httpx

from rag_portfolio.medical_release import ReleaseRegistry
from rag_portfolio.medical_release_service import create_release_app
from rag_portfolio.public_medical import canonical, write_json
from rag_portfolio.services.hybrid_contract import RetrievalResponse


async def main(args):
    if args.output.exists():
        raise ValueError("Preserve all verification attempts")
    registry = ReleaseRegistry(args.registry)
    state = registry.read()
    entry = state["releases"]["combined-business-v1"]
    registry.register(
        "combined-revocation-probe",
        entry["corpus_path"],
        entry["index_path"],
        entry["approval_path"],
    )
    registry.transition("combined-revocation-probe", "validate")
    token = secrets.token_urlsafe(40)
    app = create_release_app(args.registry, token)
    report = {
        "execution": "real Embedding + real Reranker through owned ASGI service",
        "production_changed": False,
        "checks": [],
        "status": "running",
    }
    headers = {
        "Authorization": "Bearer " + token,
        "X-Tenant-ID": "medical-business-v1",
        "X-Actor-Role": "operator",
        "X-Patient-Context": "false",
    }

    def body(name):
        value = registry.read()["releases"][name]
        return {
            "request_id": str(uuid4()),
            "query": "What is the evidence for robotic exoskeleton gait training after stroke?",
            "domain": "health",
            "scope_key": "medical-business-v1",
            "corpus_version": value["corpus_version"],
            "corpus_sha256": value["corpus_sha256"],
            "index_sha256": value["index_sha256"],
            "strategy": "hybrid_rerank",
            "top_k": 5,
            "context_budget": 12000,
        }

    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://owned", timeout=60
            ) as client,
        ):
            path = "/v1/experimental/hybrid/retrieve"

            async def check(label, name, expected=200, extra_headers=None):
                response = await client.post(
                    path, json=body(name), headers=extra_headers or headers
                )
                assert response.status_code == expected, (label, response.status_code)
                value = response.json()
                row = {"label": label, "http": response.status_code, "code": value.get("code")}
                if expected == 200:
                    parsed = RetrievalResponse.model_validate(value)
                    assert parsed.evidence and parsed.model_calls == {"embedding": 1, "rerank": 1}
                    assert hmac.compare_digest(
                        value["signature"],
                        hmac.new(
                            token.encode(),
                            canonical({k: v for k, v in value.items() if k != "signature"}),
                            hashlib.sha256,
                        ).hexdigest(),
                    )
                    row.update(
                        corpus_sha256=parsed.corpus_sha256,
                        index_sha256=parsed.index_sha256,
                        corpus_version=parsed.corpus_version,
                        refs=[s.doc_ref for s in parsed.evidence],
                        model_calls=parsed.model_calls,
                        stages=parsed.stages,
                    )
                report["checks"].append(row)
                return value

            await check(
                "patient_scope_denied",
                "combined-business-v1",
                403,
                {**headers, "X-Patient-Context": "true"},
            )
            await check("stale_public_pins_denied", "public-baseline", 409)
            registry.transition("public-baseline", "rollback")
            old = await check("rollback_returns_public_versions", "public-baseline")
            assert all("@public-" in s["doc_ref"] for s in old["evidence"])
            registry.transition("combined-business-v1", "activate")
            new = await check("combined_returns_combined_versions", "combined-business-v1")
            assert all("@combined-" in s["doc_ref"] for s in new["evidence"])
            registry.transition("combined-revocation-probe", "activate")
            registry.transition("combined-revocation-probe", "revoke")
            await check("revocation_stops_serving", "combined-revocation-probe", 409)
            registry.transition("public-baseline", "rollback")
            await check("revoked_pins_not_silently_redirected", "combined-revocation-probe", 409)
            registry.transition("combined-business-v1", "rollback")
            await check("final_candidate_restored", "combined-business-v1")
        report["status"] = "PASS"
    except Exception as error:
        report["status"] = "FAIL"
        report["error_type"] = type(error).__name__
        raise
    finally:
        report["calls"] = app.state.calls
        report["registry_history"] = registry.read()["history"]
        report["final_active"] = registry.read()["active"]
        write_json(args.output, report)
        print(
            {
                "status": report["status"],
                "checks": len(report["checks"]),
                "active": report["final_active"],
            }
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--registry", type=Path, default=Path(".local/medical_business_v1/releases/registry.json")
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path(".local/medical_business_v1/releases/verification-1.json"),
    )
    asyncio.run(main(p.parse_args()))
