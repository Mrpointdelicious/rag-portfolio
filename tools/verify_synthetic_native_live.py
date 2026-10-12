"""Exercise native single-Agent with approved corpus/models and observe actual supplier bodies."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(args.meta_repo / "src"))
    from eval_synthetic_native import environment
    from meta_agent.config import Settings
    from meta_agent.llm.models import create_planner_model
    from meta_agent.preprod_security import CONCEPTS

    from rag_portfolio.medical_release import ReleaseRegistry
    from rag_portfolio.public_medical_retrieval import PublicMedicalRetriever

    if args.output.exists():
        raise ValueError("Preserve all attempts")
    args.output.mkdir(parents=True)
    report = dict(
        status="RUNNING",
        clinical_approval=False,
        semantic="NOT_ASSESSED",
        patient_data_sent_to_models=False,
        execution="Native ApplicationService + create_agent; owned TCP + approved Hybrid/model",
    )

    def save():
        (args.output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )

    save()
    registry = ReleaseRegistry(args.registry)
    entry, (corpus, index, approval), generation = registry.active()
    retriever = PublicMedicalRetriever(corpus, index, processing_approval=approval)
    settings = Settings(_env_file=args.meta_repo / ".env")
    model = create_planner_model(
        settings.model_copy(
            update={
                "planner_model": settings.agent_model or settings.planner_model,
                "planner_timeout_seconds": 90,
                "planner_temperature": 0,
                "planner_max_tokens": 1200,
            }
        )
    )
    outbound, retrievals = [], []

    async def capture(request):
        # Observe only provider JSON body; credentials in headers are never copied.
        outbound.append(dict(host=request.url.host, body=json.loads(request.content)))

    retriever.embedding.client.event_hooks["request"].append(capture)
    retriever.reranker.client.event_hooks["request"].append(capture)
    model.root_async_client._client.event_hooks["request"].append(capture)

    async def retrieve(query):
        assert query in CONCEPTS.values()
        result = await retriever.retrieve(query)
        retrievals.append({k: result[k] for k in ["actual_model_calls", "embedding", "rerank"]})
        return [
            {
                "doc_ref": retriever.chunks[r["chunk_id"]]["doc_ref"],
                "text": retriever.chunks[r["chunk_id"]]["text"],
            }
            for r in result["strategies"]["hybrid_rerank"]["top5"][:3]
        ]

    release = dict(
        release_id=entry["release_id"],
        corpus_sha256=entry["corpus_sha256"],
        index_sha256=entry["index_sha256"],
        generation=generation,
    )

    def live_release():
        try:
            current, _, current_generation = registry.active()
            return dict(
                active=True,
                release_id=current["release_id"],
                generation=current_generation,
                corpus_sha256=current["corpus_sha256"],
                index_sha256=current["index_sha256"],
            )
        except (ValueError, OSError, KeyError):
            return dict(active=False, **release)

    try:
        async with (
            environment(
                args.output / "owned.sqlite",
                retrieve=retrieve,
                model=model,
                release=release,
                release_state=live_release,
            ) as env,
            httpx.AsyncClient(trust_env=False, timeout=180) as client,
        ):
            token = env["token"](env["claims"])
            r = await client.post(
                env["url"] + "/v1/synthetic-native/runs",
                headers={"Authorization": token},
                json=dict(task="combined", session_id="syn-session-a", concept="robotic_gait"),
            )
            data = r.json()
            serialized = json.dumps(outbound, ensure_ascii=False)
            checks = dict(
                http_200=r.status_code == 200,
                patient_record_preserved=data.get("patient", {})
                .get("measurements", [{}])[0]
                .get("value")
                == 0.42,
                live_sources_returned=bool(data.get("literature", {}).get("snippets")),
                no_generated_claims_published=data.get("generated_answer") is None,
                no_patient_canaries_in_observed_provider_bodies=all(
                    c not in serialized
                    for c in ["syn-p-a", "syn-self", "合成同名患者", "syn-session-a", "+86-000"]
                ),
                real_generation=bool(env["app"].state.generated_reviews),
                real_embedding_rerank=bool(
                    retrievals
                    and retrievals[0]["actual_model_calls"]["embedding"]
                    and retrievals[0]["actual_model_calls"]["rerank"]
                ),
            )
            report.update(
                checks=checks,
                http=r.status_code,
                release=release,
                retrievals=retrievals,
                provider_requests=[
                    dict(
                        host=o["host"],
                        body_sha256=hashlib.sha256(
                            json.dumps(o["body"], sort_keys=True).encode()
                        ).hexdigest(),
                    )
                    for o in outbound
                ],
            )
            if data.get("run_id"):
                native = await env["container"].repository.run(
                    env["app"].state.policies[data["run_id"]].scope.scope_hash, data["run_id"]
                )
                checks["native_agent_completed"] = native.status in {"succeeded", "partial"}
                checks["native_agent_model_calls"] = native.metrics.get("llm_calls", 0) >= 2
                checks["native_tools_used"] = {
                    r.outputs.get("tool_name") for r in native.results.values()
                } >= {"get_patient_profile", "get_irego_session_analysis", "search_knowledge"}
                ref = data["literature"]["snippets"][0]["doc_ref"]
                citation = await client.get(
                    env["url"] + "/v1/synthetic-native/runs/" + data["run_id"] + "/citation",
                    headers={"Authorization": token},
                    params={"ref": ref},
                )
                checks["live_citation_resolves"] = citation.status_code == 200
            (args.output / "private-model-review.json").write_text(
                json.dumps(
                    dict(
                        generated=env["app"].state.generated_reviews,
                        observed_provider_bodies=outbound,
                    ),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf8",
            )
            report["status"] = (
                "PASS_engineering_semantic_pending" if all(checks.values()) else "FAIL"
            )
    except Exception as exc:
        report.update(status="FAIL", error_type=type(exc).__name__)
        raise
    finally:
        await retriever.close()
        await model.root_async_client.close()
        save()
    print(
        json.dumps(
            dict(
                status=report["status"], checks=report.get("checks"), provider_calls=len(outbound)
            ),
            ensure_ascii=False,
        )
    )
    if report["status"] != "PASS_engineering_semantic_pending":
        raise ValueError("Live contract failed")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
