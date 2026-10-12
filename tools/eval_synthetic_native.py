"""Sealed native single-Agent synthetic integration over owned TCP services."""

import argparse
import asyncio
import copy
import hashlib
import hmac
import json
import secrets
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from eval_synthetic_preproduction import serve

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def environment(
    path, *, model=None, retrieve=None, release=None, release_state=None, tour=False
):
    from fastapi import HTTPException, Request
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from meta_agent.agent.runtime import SingleAgentRuntime
    from meta_agent.config import Settings
    from meta_agent.infrastructure.container import create_container
    from meta_agent.preprod_api import AuthorityClient, app_base, bearer, create_authority_app
    from meta_agent.preprod_native import create_native_app
    from meta_agent.preprod_security import CONCEPTS, Rejected, State, digest, sign
    from pydantic import Field

    from rag_portfolio.preproduction_service import create_preproduction_app

    class SyntheticModel(BaseChatModel):
        inputs: list = Field(default_factory=list)
        bad_query: bool = False

        @property
        def _llm_type(self):
            return "owned-synthetic-native"

        def bind_tools(self, *args, **kw):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kw):
            self.inputs.append([m.model_dump(mode="json") for m in messages])
            payload = json.loads(messages[-1].content)
            output = AIMessage(content="文献示例0.93 m/s不是个人测量。")
            if not payload["sources"]:
                query = "手机号+86-000 syn-p-a" if self.bad_query else payload["question"]
                output = AIMessage(
                    content="",
                    tool_calls=[
                        dict(
                            id="search-1",
                            name="search_knowledge",
                            args=dict(query=query, domain="health"),
                        )
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=output)])

    state = State(path)
    keys = {a: secrets.token_urlsafe(40) for a in ["agent", "patient-tools", "knowledge"]}
    service_tokens = {a: secrets.token_urlsafe(40) for a in keys}
    response_key = secrets.token_urlsafe(40)
    generation = state.grant("syn-tenant", "syn-self", "syn-p-a")
    now = int(time.time())
    claims = dict(
        schema_version="synthetic-subject-v1",
        issuer="owned-synthetic-authority",
        audience="agent",
        tenant="syn-tenant",
        actor="syn-self",
        patient="syn-p-a",
        role="patient",
        purpose="record_and_literature",
        session="syn-session-a",
        grant_generation=generation,
        issued_at=now,
        expires_at=now + 120,
    )
    refs = {
        ("syn-tenant", "syn-p-a"): "opaque-synthetic-a",
        ("syn-tenant", "syn-p-b"): "opaque-synthetic-b",
    }
    metric = dict(
        metric_code="walking_speed",
        normalized_value=0.42,
        raw_value=0.42,
        value_status="valid",
        unit="m/s",
        unit_status="confirmed",
        source_kind="raw",
    )
    body = dict(
        tool_name="get_irego_session_analysis",
        contract_version="1.6.0",
        status="success",
        data=dict(
            session=dict(
                patient_ref=refs[("syn-tenant", "syn-p-a")], session_ref="irego_s_v1_synthetic-a"
            ),
            time=dict(completed_at=datetime.now(UTC).isoformat()),
            report_blocks=[dict(metrics=[metric])],
        ),
    )
    measurement_tool = "get_iretour_session_analysis" if tour else "get_irego_session_analysis"
    if tour:
        metric = dict(
            metric_code="gait_speed",
            normalized_value=0.42,
            raw_value=0.42,
            value_status="valid",
            unit=None,
            registry_version="iretour-metric-1.0.0",
        )
        body = dict(
            tool_name=measurement_tool,
            contract_version="1.6.0",
            status="partial",
            meta={"registry_versions": {"contract": "iretour-1.0.0"}},
            data=dict(
                session=dict(
                    patient_ref=refs[("syn-tenant", "syn-p-a")],
                    session_ref="iretour_s_v1_synthetic-a",
                ),
                time=dict(execution_ended_at=datetime.now(UTC).isoformat()),
                result=dict(result_state="summary_available", metrics=[metric]),
                quality=dict(usable=True),
            ),
        )
    records = {
        ("syn-tenant", "syn-p-a"): body,
        ("syn-tenant", "syn-p-b"): {
            **copy.deepcopy(body),
            "data": {
                **copy.deepcopy(body["data"]),
                "session": dict(
                    patient_ref="opaque-synthetic-b", session_ref="irego_s_v1_synthetic-b"
                ),
                "report_blocks": [dict(metrics=[{**metric, "normalized_value": 0.77}])],
            },
        },
    }
    active = dict(
        active=True,
        release_id="syn-release-1",
        generation=1,
        corpus_sha256="c" * 64,
        index_sha256="i" * 64,
    )
    if release:
        active.update(release)
    model = model or SyntheticModel()
    container = await create_container(
        Settings(
            _env_file=None,
            app_env="test",
            dry_run=True,
            orchestration_mode="legacy",
            persistence_backend="memory",
            planner_mode="deterministic",
            request_timeout_seconds=180,
        )
    )
    container.application.agent_runtime = SingleAgentRuntime(
        model, checkpointer=container.repository.checkpointer, store=container.repository.store
    )
    try:
        async with AsyncExitStack() as stack:
            auth_url = await stack.enter_async_context(
                serve(create_authority_app(state, keys, service_tokens))
            )
            authorities = {a: AuthorityClient(auth_url, service_tokens[a]) for a in keys}
            patient_app = app_base()
            patient_app.state.calls = []

            @patient_app.post("/v1/synthetic-webapi/{endpoint}")
            async def patient(endpoint: str, request: Request):
                header = request.headers.get("authorization", "")
                subject = await authorities["patient-tools"].authorize(header)
                payload = await request.json()
                if (
                    endpoint not in {"get_patient_profile", measurement_tool}
                    or payload.get("system_context", {}).get("user") != subject.patient
                ):
                    raise Rejected(403, "synthetic_tool_subject_conflict")
                patient_app.state.calls.append(endpoint)
                raw = records[(subject.tenant, subject.patient)]
                if endpoint == "get_patient_profile":
                    raw = dict(
                        tool_name=endpoint,
                        contract_version="patient-layers-1.0.0",
                        status="success",
                        data=dict(
                            patient_ref="project:" + subject.patient,
                            profile=dict(
                                name="合成同名患者",
                                phone="+86-000",
                                history="PRIVATE_HISTORY_CANARY",
                            ),
                        ),
                    )
                await authorities["patient-tools"].authorize(header)
                return dict(subject=digest([subject.tenant, subject.patient]), body=raw)

            patient_url = await stack.enter_async_context(serve(patient_app))

            async def authorize(header):
                try:
                    return (await authorities["knowledge"].authorize(header)).model_dump()
                except Rejected as exc:
                    raise HTTPException(exc.status, exc.code) from exc

            async def synthetic_retrieve(query):
                assert query in CONCEPTS.values()
                return [
                    dict(doc_ref="syn-paper@v1#outcome", text="自建合成文献0.93 m/s不是患者实测。")
                ]

            knowledge = create_preproduction_app(
                authorize=authorize,
                release_state=release_state or (lambda: dict(active)),
                retrieve=retrieve or synthetic_retrieve,
                response_key=response_key,
            )
            knowledge_url = await stack.enter_async_context(serve(knowledge))
            factory_args = dict(
                measurement_tool=measurement_tool,
                container=container,
                state=state,
                keys=keys,
                authority=authorities["agent"],
                patient_url=patient_url,
                knowledge_url=knowledge_url,
                response_key=response_key,
                expected_pins={k: active[k] for k in ["corpus_sha256", "index_sha256"]},
                patient_refs=refs,
            )
            app = create_native_app(**factory_args)
            url = await stack.enter_async_context(serve(app))
            yield dict(
                state=state,
                claims=claims,
                token=lambda c: bearer(sign(c, keys["agent"])),
                app=app,
                url=url,
                model=model,
                container=container,
                release=active,
                knowledge=knowledge,
                patient_app=patient_app,
                records=records,
                body=body,
                metric=metric,
                stack=stack,
                factory_args=factory_args,
                response_key=response_key,
            )
    finally:
        await container.close()


async def run_case(case, path):
    from langchain_core.messages import HumanMessage, messages_to_dict
    from meta_agent.preprod_native import create_native_app
    from meta_agent.preprod_security import canonical

    scenario = case["scenario"]
    async with (
        environment(path, tour=scenario.startswith("iretour_")) as env,
        httpx.AsyncClient(trust_env=False, timeout=25) as client,
    ):
        claims, app = env["claims"], env["app"]
        headers = {"Authorization": env["token"](claims)}
        payload = dict(task="combined", session_id=claims["session"], concept="robotic_gait")
        url = env["url"] + "/v1/synthetic-native/runs"

        def revoke():
            env["state"].grant(claims["tenant"], claims["actor"], claims["patient"], active=False)

        async def post():
            return await client.post(url, headers=headers, json=payload)

        async def changed():
            env["release"]["generation"] += 1

        async def revoked():
            revoke()

        if scenario == "record_local_only":
            payload["task"], payload["concept"] = "record", None
        if scenario == "literature_positive":
            payload["task"] = "literature"
        if scenario in {"free_query_rejected", "patient_override_rejected"}:
            payload["query" if scenario == "free_query_rejected" else "patient_id"] = (
                "PRIVATE_QUERY_CANARY"
            )
        if scenario == "wrong_session":
            payload["session_id"] = "another"
        if scenario == "revoked_before_start":
            revoke()
        if scenario == "expired_subject":
            headers["Authorization"] = env["token"](
                {**claims, "issued_at": int(time.time()) - 100, "expires_at": int(time.time()) - 1}
            )
        if scenario == "revoked_before_model":
            app.state.before_model = revoked
        if scenario == "revoked_before_publish":
            app.state.before_publish = revoked
        if scenario == "source_generation_publish":
            app.state.before_publish = changed
        if scenario.startswith("source_") and scenario not in {
            "source_generation_snapshot",
            "source_generation_publish",
        }:

            def fault(value):
                value = copy.deepcopy(value)
                if scenario == "source_signature":
                    value["signature"] = "0" * 64
                    return value
                if scenario == "source_hash":
                    value["snippets"][0]["text"] += "changed"
                if scenario == "source_pin":
                    value["release"]["index_sha256"] = "x" * 64
                if scenario == "source_expiry":
                    value["expires_at"] = int(time.time()) - 1
                value["signature"] = hmac.new(
                    env["response_key"].encode(),
                    canonical({k: v for k, v in value.items() if k != "signature"}),
                    "sha256",
                ).hexdigest()
                return value

            env["knowledge"].state.response_transform = fault
        if scenario == "patient_ref_conflict":
            env["body"]["data"]["session"]["patient_ref"] = "opaque-synthetic-b"
        if scenario == "patient_unit_unknown":
            env["metric"]["unit_status"] = "unknown"
        if scenario == "patient_placeholder_zero":
            env["metric"].update(normalized_value=0.0, value_status="default_zero_unusable")
        if scenario == "patient_real_zero":
            env["metric"]["normalized_value"] = 0.0
        if scenario == "iretour_no_result":
            env["body"]["data"]["result"]["result_state"] = "no_result"
        if scenario == "iretour_plan_only_not_measurement":
            env["body"]["data"]["result"].update(result_state="no_result", metrics=[])
            env["body"]["data"]["plan"] = {"parameters": [{"code": "speed", "value": "0.93"}]}
        if scenario == "iretour_invalid_registry":
            env["metric"]["registry_version"] = "unverified"
        if scenario == "iretour_invalid_contract":
            env["body"]["meta"]["registry_versions"]["contract"] = "unverified"
        if scenario == "iretour_patient_ref_conflict":
            env["body"]["data"]["session"]["patient_ref"] = "opaque-synthetic-b"
        if scenario == "query_from_model_rejected":
            env["model"].bad_query = True
        if scenario in {
            "profile_cache_positive",
            "history_minimization",
            "new_grant_fresh_cache",
            "cache_tamper",
        }:
            initial = await post()
            assert initial.status_code == 200
            saved = env["state"].get("native_run", initial.json()["run_id"])
            policy = app.state.policies[initial.json()["run_id"]]
            if scenario == "history_minimization":
                thread = policy.scope.thread_id(hashlib.sha256(b"unused").hexdigest())
                # Use the actual native conversation binding, not an unrelated memory key.
                native = await env["container"].repository.run(
                    saved["native_scope"], initial.json()["run_id"]
                )
                thread = policy.scope.thread_id(native.conversation_id)
                memory = await env["container"].repository.conversation(thread)
                memory.agent_messages = messages_to_dict(
                    [HumanMessage(content="PRIVATE_HISTORY_CANARY +86-000 syn-p-a 0.42")]
                )
                await env["container"].repository.save_conversation(thread, memory)
            if scenario == "new_grant_fresh_cache":
                claims["grant_generation"] = env["state"].grant(
                    claims["tenant"], claims["actor"], claims["patient"]
                )
                claims["session"] = "syn-new-session"
                headers["Authorization"] = env["token"](claims)
                payload["session_id"] = claims["session"]
            if scenario == "cache_tamper":
                repo = env["container"].repository
                cached = await repo.get("cache", saved["native_scope"], "agent:profile")
                evidence = await repo.get("evidence", saved["native_scope"], cached["evidence_id"])
                evidence["payload"]["data"]["profile"]["name"] = "changed"
                await repo.put(
                    "evidence", saved["native_scope"], cached["evidence_id"], evidence, 60
                )
        if scenario == "native_scope_policy_conflict":
            from dataclasses import replace

            original_start = env["container"].application.start

            async def corrupt_scope(request):
                return await original_start(
                    replace(request, scope=replace(request.scope, security_epoch=None))
                )

            env["container"].application.start = corrupt_scope
        response = await post()
        data = response.json()
        created = response.status_code == 200
        if created and scenario in {
            "cross_patient_snapshot",
            "revoked_snapshot",
            "revoked_citation",
            "revoked_replay",
            "source_generation_snapshot",
            "stored_result_tamper",
            "checkpoint_resume_unexposed",
            "snapshot_after_factory_reconstruction",
            "new_grant_old_session",
            "revoked_during_sse",
        }:
            run_url = url + "/" + data["run_id"]
            if scenario == "cross_patient_snapshot":
                grant = env["state"].grant(claims["tenant"], claims["actor"], "syn-p-b")
                headers["Authorization"] = env["token"](
                    {
                        **claims,
                        "patient": "syn-p-b",
                        "session": "syn-session-b",
                        "grant_generation": grant,
                    }
                )
            if scenario.startswith("revoked_") and scenario != "revoked_during_sse":
                revoke()
            if scenario == "source_generation_snapshot":
                await changed()
            if scenario == "stored_result_tamper":
                saved = env["state"].get("native_run", data["run_id"])
                saved["result"]["patient"]["measurements"][0]["value"] = 99
                env["state"].put("native_run", data["run_id"], saved)
            if scenario == "new_grant_old_session":
                claims["grant_generation"] = env["state"].grant(
                    claims["tenant"], claims["actor"], claims["patient"]
                )
                headers["Authorization"] = env["token"](claims)
                response = await post()
            elif scenario == "revoked_during_sse":
                gate, entered = asyncio.Event(), asyncio.Event()

                async def wait_gate():
                    entered.set()
                    await gate.wait()

                app.state.stream_gate = wait_gate
                async with client.stream("GET", run_url + "/events", headers=headers) as stream:
                    lines = stream.aiter_lines()
                    assert await anext(lines) == "event: accepted"
                    await entered.wait()
                    revoke()
                    gate.set()
                    text = "\n".join([line async for line in lines])
                    assert "native_run_id" not in text and "event: failure" in text
                    return dict(
                        http=stream.status_code,
                        positive_native_completed=True,
                        subsequent_event_blocked=True,
                    )
            elif scenario == "checkpoint_resume_unexposed":
                response = await client.post(run_url + "/resume", headers=headers, json={})
            else:
                if scenario == "snapshot_after_factory_reconstruction":
                    app2 = create_native_app(**env["factory_args"])
                    next_url = await env["stack"].enter_async_context(serve(app2))
                    run_url = next_url + "/v1/synthetic-native/runs/" + data["run_id"]
                suffix = (
                    "/citation?ref=syn-paper@v1%23outcome"
                    if scenario == "revoked_citation"
                    else "/events"
                    if scenario == "revoked_replay"
                    else ""
                )
                response = await client.get(run_url + suffix, headers=headers)
        if response.status_code == 200:
            result = response.json()
            if scenario != "snapshot_after_factory_reconstruction":
                assert result["native_status"] in {"succeeded", "partial"}
            if scenario in {"iretour_no_result", "iretour_plan_only_not_measurement"}:
                assert result["patient"]["absence"] == "no_record"
                assert result["patient"]["measurements"] == []
            elif payload["task"] in {"record", "combined"}:
                m = result["patient"]["measurements"][0]
                expected = (
                    "unit_unverified"
                    if scenario in {"patient_unit_unknown", "iretour_unit_unknown"}
                    else "placeholder"
                    if scenario == "patient_placeholder_zero"
                    else "recorded"
                )
                assert m["state"] == expected
                assert m["value"] == (
                    None
                    if expected != "recorded"
                    else 0.0
                    if scenario == "patient_real_zero"
                    else 0.42
                )
            assert result["generated_answer"] is None
            if scenario == "profile_cache_positive":
                assert env["patient_app"].state.calls.count("get_patient_profile") == 1
            if scenario == "new_grant_fresh_cache":
                assert env["patient_app"].state.calls.count("get_patient_profile") == 2
            if scenario == "record_local_only":
                assert not env["model"].inputs and not env["knowledge"].state.outbound
            if scenario == "literature_positive":
                assert not env["patient_app"].state.calls
        serialized = json.dumps(env["model"].inputs, ensure_ascii=False)
        assert all(
            c not in serialized
            for c in [
                "syn-p-a",
                "syn-self",
                "syn-session-a",
                "合成同名患者",
                "+86-000",
                "PRIVATE_HISTORY_CANARY",
                "PRIVATE_QUERY_CANARY",
                "0.42",
            ]
        )
        if response.status_code != 200:
            assert all(c not in response.text for c in ["measurements", "syn-p-a", "0.42", "0.93"])
        if scenario == "query_from_model_rejected":
            assert not env["knowledge"].state.outbound
        return dict(
            http=response.status_code,
            positive_native_completed=created,
            model_calls=len(env["model"].inputs),
            patient_field_leaks=0,
            knowledge_requests=len(env["knowledge"].state.outbound),
        )


async def main(args):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(args.meta_repo / "src"))
    if args.output.exists():
        raise ValueError("Keep every attempt")
    args.output.mkdir(parents=True)
    folder = ROOT / "eval/synthetic_native_v2"
    seal = json.loads((folder / "seal.json").read_bytes())
    raw = (folder / "cases.jsonl").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == seal["cases_sha256"]
    tour_raw = (folder / "iretour-cases.jsonl").read_bytes()
    assert (
        hashlib.sha256(tour_raw).hexdigest()
        == json.loads((folder / "iretour-seal.json").read_bytes())["cases_sha256"]
    )
    raw += tour_raw
    report = dict(status="RUNNING", cases=[], clinical_approval=False, semantic="NOT_ASSESSED")
    for case in [json.loads(line) for line in raw.splitlines()]:
        try:
            result = await run_case(case, args.output / (case["case_id"] + ".sqlite"))
            result["passed"] = result["http"] == case["expected_http"]
        except Exception as exc:
            import traceback

            (args.output / (case["case_id"] + "-error.txt")).write_text(
                traceback.format_exc(), encoding="utf8"
            )
            result = dict(passed=False, error_type=type(exc).__name__)
        report["cases"].append({**case, **result})
        (args.output / "result.json").write_bytes(
            (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
        )
        print({"case": case["case_id"], **result}, flush=True)
    report["status"] = "PASS" if all(c["passed"] for c in report["cases"]) else "FAIL"
    (args.output / "result.json").write_bytes(
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    )
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--meta-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
