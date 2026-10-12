"""Real unchanged MetaAgent via TCP; sealed questions only, never Gold in model input."""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf8")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def ready(url, process=None):
    for _ in range(120):
        if process and process.poll() is not None:
            raise RuntimeError("Owned service exited")
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                if (await client.get(url + "/health/live")).status_code == 200:
                    return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.15)
    raise RuntimeError("Owned service unavailable")


async def main(args):
    sys.path.insert(0, str(args.meta_repo / "src"))
    from langchain_core.callbacks import AsyncCallbackHandler
    from meta_agent.agent.runtime import SingleAgentRuntime
    from meta_agent.app import create_app
    from meta_agent.config import Settings
    from meta_agent.llm.models import create_planner_model
    from meta_agent.orchestration.identity import TrustedScope

    if args.output.exists():
        raise ValueError("Preserve all attempts; use new output directory")
    args.output.mkdir(parents=True)
    seal = load(args.rag_repo / "eval/medical_business_v1/seal.json")
    case_path = args.rag_repo / "eval/medical_business_v1" / f"{args.split}.jsonl"
    if hashlib.sha256(case_path.read_bytes()).hexdigest() != seal[f"{args.split}_cases_sha256"]:
        raise ValueError("Sealed business question drift")
    cases = [json.loads(line) for line in case_path.read_bytes().splitlines()]
    state = load(args.registry)
    entry = state["releases"][state["active"]]
    if entry["corpus_sha256"] != seal["corpus_sha256"]:
        raise ValueError("Agent release differs from sealed business corpus")
    existing = Settings(_env_file=args.meta_repo / ".env")
    token, agent_token = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
    rp, ap = free_port(), free_port()
    rag_url, agent_url = f"http://127.0.0.1:{rp}", f"http://127.0.0.1:{ap}"
    fault_file = args.output / "fault-mode.json"
    save(fault_file, {"mode": "none"})
    settings = Settings(
        _env_file=None,
        app_env="test",
        dry_run=True,
        orchestration_mode="agent",
        service_bearer_token=agent_token,
        deepseek_api_key=existing.deepseek_api_key,
        deepseek_base_url=existing.deepseek_base_url,
        agent_model=existing.agent_model or existing.planner_model,
        planner_model=existing.planner_model,
        planner_temperature=0.0,
        planner_max_tokens=2048,
        request_timeout_seconds=180,
        agent_model_timeout_seconds=90,
        agent_max_model_calls=8,
        prompt_bundle="business-grounded-v9",
        business_grounding_enabled=True,
        knowledge_backend="remote_hybrid",
        knowledge_rag_url=rag_url + "/v1/experimental/hybrid/retrieve",
        knowledge_rag_token=token,
        knowledge_rag_tenant_id="medical-business-v1",
        knowledge_rag_scope_key="medical-business-v1",
        knowledge_rag_corpus_version=entry["corpus_version"],
        knowledge_rag_corpus_sha256=entry["corpus_sha256"],
        knowledge_hybrid_index_sha256=entry["index_sha256"],
        knowledge_hybrid_context_budget=12000,
        knowledge_hybrid_strategy="hybrid_rerank",
        knowledge_hybrid_domains=["health", "help"],
        knowledge_rag_timeout_seconds=15,
        knowledge_rag_top_k=5,
        agent_tool_output_tokens=5000,
        doctors_enabled=False,
        scene_enabled=False,
    )
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ap, log_level="warning"))
    inputs, outputs, active = {}, {}, {}

    async def observe(ctx, messages):
        active["run_id"] = ctx.record.run_id
        inputs.setdefault(ctx.record.run_id, []).append(
            [
                {"type": m.type, "content": m.content, "tool_calls": getattr(m, "tool_calls", [])}
                for m in messages
            ]
        )

    class Capture(AsyncCallbackHandler):
        async def on_llm_end(self, response, **kwargs):
            for batch in response.generations:
                for generation in batch:
                    message = getattr(generation, "message", None)
                    if message is not None:
                        outputs.setdefault(active["run_id"], []).append(
                            {
                                "content": message.content,
                                "tool_calls": getattr(message, "tool_calls", []),
                                "usage": getattr(message, "usage_metadata", None),
                            }
                        )

    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "split": args.split,
        "execution": (
            "real unchanged single Agent + owned TCP medical release service + real models"
        ),
        "model": settings.agent_model,
        "prompt_bundle": settings.prompt_bundle,
        "corpus_sha256": entry["corpus_sha256"],
        "index_sha256": entry["index_sha256"],
        "meta_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=args.meta_repo, text=True
        ).strip(),
        "clinical_approval": False,
        "semantic_review": "pending independent human",
        "budget": {"context": 12000, "unit": "UTF-8 bytes upper bound", "model_calls": 8},
        "cases": [],
        "status": "running",
    }
    log = (args.output / "rag-service.log").open("w", encoding="utf8")
    process = subprocess.Popen(
        [
            str(args.rag_repo / ".venv/Scripts/python.exe"),
            "-X",
            "utf8",
            "tools/serve_medical_release.py",
            "--port",
            str(rp),
            "--registry",
            str(args.registry),
            "--fault-file",
            str(fault_file.resolve()),
            "--trace",
            str((args.output / "service-trace.json").resolve()),
        ],
        cwd=args.rag_repo,
        env={**os.environ, "MEDICAL_RELEASE_TEST_TOKEN": token, "PYTHONUTF8": "1"},
        stdout=log,
        stderr=log,
    )
    agent_task = asyncio.create_task(server.serve())
    try:
        await ready(rag_url, process)
        await ready(agent_url)
        container = app.state.container
        model = create_planner_model(
            settings.model_copy(
                update={
                    "planner_model": settings.agent_model,
                    "planner_timeout_seconds": 90,
                    "planner_max_retries": 0,
                }
            )
        )
        model.callbacks = [Capture()]
        container.application.agent_runtime = SingleAgentRuntime(
            model,
            checkpointer=container.repository.checkpointer,
            store=container.repository.store,
            model_input_observer=observe,
        )
        scope = TrustedScope("medical-business-v1", "technical-reviewer", role="operator")
        for case in cases:
            conversation = case["case_id"] + "-" + uuid4().hex
            known = {}
            expired = []
            for number, turn in enumerate(case["turns"], 1):
                if turn.get("transition") == "expire_knowledge":
                    for eid, ev in list(known.items()):
                        await container.repository.delete("evidence", scope.scope_hash, eid)
                        expired.extend(s["doc_ref"] for s in ev["payload"].get("snippets", []))
                        del known[eid]
                save(fault_file, {"mode": turn["fault"]})
                body = {
                    "request_id": "medical-business-" + uuid4().hex,
                    "conversation_id": conversation,
                    "user": "technical-reviewer",
                    "inputs": {"tenantId": "medical-business-v1", "role": "operator"},
                    "query": turn["question"],
                    "response_mode": "blocking",
                }
                started = time.perf_counter()
                async with httpx.AsyncClient(trust_env=False, timeout=195) as client:
                    response = await client.post(
                        agent_url + "/v1/chat",
                        json=body,
                        headers={"Authorization": "Bearer " + agent_token},
                    )
                snapshot = response.json()
                if "run_id" not in snapshot:
                    raise RuntimeError("Agent returned no auditable run ID")
                record = await container.repository.run(scope.scope_hash, snapshot["run_id"])
                evidence = []
                for eid in {eid for r in record.results.values() for eid in r.evidence_ids}:
                    ev = await container.repository.evidence(scope.scope_hash, eid)
                    if ev:
                        value = ev.model_dump(mode="json")
                        evidence.append(value)
                        known[eid] = value
                valid = []
                for eid, value in known.items():
                    if await container.repository.evidence(scope.scope_hash, eid):
                        valid.append(value)
                row = {
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "turn": number,
                    "question": turn["question"],
                    "fault": turn["fault"],
                    "http": response.status_code,
                    "status": record.status,
                    "run_id": record.run_id,
                    "answer": "\n".join(
                        e.payload["text"] for e in record.events if e.type == "answer_part"
                    ),
                    "model_inputs": inputs.get(record.run_id, []),
                    "model_outputs": outputs.get(record.run_id, []),
                    "tool_results": [r.model_dump(mode="json") for r in record.results.values()],
                    "evidence": evidence,
                    "valid_conversation_evidence": valid,
                    "expired_refs": list(expired),
                    "metrics": record.metrics,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                }
                report["cases"].append(row)
                save(args.output / "cases.json", report)
                print(
                    {
                        "case": case["case_id"],
                        "turn": number,
                        "status": record.status,
                        "model_calls": len(row["model_outputs"]),
                        "evidence": len(evidence),
                    },
                    flush=True,
                )
        report["status"] = "DONE_execution_semantic_review_pending"
    except Exception as error:
        report["status"] = "execution_error"
        report["error_type"] = type(error).__name__
        raise
    finally:
        save(fault_file, {"mode": "none"})
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()
        server.should_exit = True
        await agent_task
        report["owned_processes_stopped"] = process.poll() is not None
        report["finished_at"] = datetime.now(UTC).isoformat()
        save(args.output / "cases.json", report)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--rag-repo", type=Path, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--split", choices=["dev", "heldout"], required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
