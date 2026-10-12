"""Exercise sealed shared authority cases using owned OS processes and PostgreSQL only."""

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
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def main(args):
    from meta_agent.preprod_api import bearer, decode
    from meta_agent.preprod_security import sign
    from meta_agent.preprod_shared import PostgresState

    out = args.output.resolve()
    if out.exists():
        raise ValueError("Preserve all attempts; use a new output directory")
    out.mkdir(parents=True)
    sealed = ROOT / "eval/shared_authority_v3/cases.jsonl"
    seal = json.loads(sealed.with_name("seal.json").read_bytes())
    assert hashlib.sha256(sealed.read_bytes()).hexdigest() == seal["cases_sha256"]
    cases = {r["id"]: r for r in map(json.loads, sealed.read_bytes().splitlines())}
    addendum = sealed.with_name("addendum-cases.jsonl")
    addendum_seal = json.loads(addendum.with_name("addendum-seal.json").read_bytes())
    assert hashlib.sha256(addendum.read_bytes()).hexdigest() == addendum_seal["cases_sha256"]
    cases.update({r["id"]: r for r in map(json.loads, addendum.read_bytes().splitlines())})
    recovery = sealed.with_name("recovery-cases.jsonl")
    recovery_seal = json.loads(recovery.with_name("recovery-seal.json").read_bytes())
    assert hashlib.sha256(recovery.read_bytes()).hexdigest() == recovery_seal["cases_sha256"]
    cases.update({r["id"]: r for r in map(json.loads, recovery.read_bytes().splitlines())})
    result = dict(
        cases=[],
        process_starts=[],
        synthetic_only=True,
        actual_supplier_requests=0,
        clinical_approval=False,
        seal_sha256=hashlib.sha256(sealed.with_name("seal.json").read_bytes()).hexdigest(),
        addendum_seal_sha256=hashlib.sha256(
            addendum.with_name("addendum-seal.json").read_bytes()
        ).hexdigest(),
        recovery_seal_sha256=hashlib.sha256(
            recovery.with_name("recovery-seal.json").read_bytes()
        ).hexdigest(),
    )
    private = []
    processes, files, cid = {}, [], None
    python = args.meta_repo / ".venv/Scripts/python.exe"
    worker = args.meta_repo / "tools/shared_authority_worker.py"
    ports = {r: free_port() for r in ["authority", "agent", "patient", "knowledge", "postgres"]}
    urls = {r: f"http://127.0.0.1:{p}" for r, p in ports.items()}
    keys = {a: secrets.token_urlsafe(40) for a in ["agent", "patient-tools", "knowledge"]}
    service_tokens = {a: secrets.token_urlsafe(40) for a in keys}
    login_tokens = {
        a: secrets.token_urlsafe(40) for a in ["self", "clinician", "operator", "other", "tenant"]
    }
    password = secrets.token_urlsafe(30)
    dsn = f"postgresql://synthetic:{password}@127.0.0.1:{ports['postgres']}/synthetic_authority_v3"
    config = dict(
        authority_dsn=dsn,
        agent_dsn=dsn.rsplit("/", 1)[0] + "/metaagent_test",
        keys=keys,
        service_tokens=service_tokens,
        response_key=secrets.token_urlsafe(40),
        authority_url=urls["authority"],
        patient_url=urls["patient"],
        knowledge_url=urls["knowledge"],
        patient_refs=[
            ["syn-tenant", "101", "opaque-synthetic-101"],
            ["syn-tenant", "102", "opaque-synthetic-102"],
        ],
        expected_pins=dict(corpus_sha256="c" * 64, index_sha256="i" * 64),
        release=dict(
            active=True,
            release_id="syn-release-1",
            generation=1,
            corpus_sha256="c" * 64,
            index_sha256="i" * 64,
        ),
        logins={
            login_tokens[k]: [
                "syn-other" if k == "tenant" else "syn-tenant",
                "syn-" + k,
                "clinician" if k == "clinician" else "operator" if k == "operator" else "patient",
            ]
            for k in login_tokens
        },
    )
    config_path = out / "owned-config.json"
    save(config_path, config)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "SYNTHETIC_ONLY": "1",
        "AUTHORITY_URL": urls["authority"],
        "AUTHORITY_SERVICE_TOKEN": service_tokens["patient-tools"],
        "PYTHONPATH": os.pathsep.join([str(args.meta_repo / "src"), str(ROOT / "src")]),
    }
    counts = {}

    def checkpoint():
        save(out / "result.json", result)
        save(out / "private-http-review.json", private)

    def record(case, ok, **evidence):
        assert case not in {r["id"] for r in result["cases"]}
        result["cases"].append(dict(**cases[case], passed=bool(ok), evidence=evidence))
        checkpoint()
        print(dict(case=case, passed=bool(ok)), flush=True)

    def start(role, cmd=None, name=None):
        name = name or role
        counts[name] = counts.get(name, 0) + 1
        log = (out / f"{name}-{counts[name]}.log").open("wb")
        files.append(log)
        if cmd is None:
            cmd = [
                str(python),
                "-X",
                "utf8",
                str(worker),
                "--config",
                str(config_path),
                "--role",
                role,
                "--port",
                str(ports.get(role, 0)),
            ]
        process = subprocess.Popen(
            cmd,
            env=env,
            cwd=args.meta_repo,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        processes[name] = process
        result["process_starts"].append(dict(role=name, pid=process.pid, start=counts[name]))
        checkpoint()
        return process

    def stop(name):
        process = processes.pop(name, None)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=15)

    async def ready(role):
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if processes[role].poll() is not None:
                    raise RuntimeError(f"{role} exited; retained process log")
                try:
                    if (await client.get(urls[role] + "/health")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
        raise RuntimeError(f"{role} did not become ready")

    async def http(role, path, header="", body=None):
        async with httpx.AsyncClient(trust_env=False, timeout=90) as client:
            r = await client.request(
                "POST" if body is not None else "GET",
                urls[role] + path,
                headers={"Authorization": header},
                json=body,
            )
        try:
            value = r.json()
        except ValueError:
            value = r.text
        private.append(dict(service=role, path=path, status=r.status_code, response=value))
        checkpoint()
        return r.status_code, value

    async def login(actor="self", session="syn-session-a", **extra):
        return await http(
            "authority",
            "/sessions",
            "Bearer " + login_tokens[actor],
            dict(patient="101", session=session, **extra),
        )

    def delegate(header, audience="patient-tools", **updates):
        claims = {**decode(header)["claims"], "audience": audience, **updates}
        return bearer(sign(claims, keys[audience]))

    async def patient(header, user="101", session="syn-session-a"):
        return await http(
            "patient",
            "/api/ai/patients/tools/get_patient_profile",
            header,
            dict(system_context=dict(user=user, conversation_id=session)),
        )

    async def run(header, session, task="combined"):
        return await http(
            "agent",
            "/v1/synthetic-native/runs",
            header,
            dict(
                session_id=session, task=task, concept="robotic_gait" if task != "record" else None
            ),
        )

    try:
        name = "synthetic-shared-v3-" + uuid4().hex[:12]
        created = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "codex.synthetic.shared=owned",
                "-p",
                f"127.0.0.1:{ports['postgres']}:5432",
                "--tmpfs",
                "/var/lib/postgresql/data:rw",
                "-e",
                "POSTGRES_USER=synthetic",
                "-e",
                "POSTGRES_PASSWORD=" + password,
                "-e",
                "POSTGRES_DB=synthetic_authority_v3",
                "postgres:15-alpine",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if created.returncode:
            raise RuntimeError("Owned PostgreSQL creation failed")
        cid = created.stdout.strip()
        for _ in range(100):
            try:
                with psycopg.connect(dsn, connect_timeout=1, autocommit=True) as db:
                    db.execute("CREATE DATABASE metaagent_test")
                break
            except psycopg.OperationalError:
                await asyncio.sleep(0.3)
        else:
            raise RuntimeError("Owned PostgreSQL unavailable")
        state = PostgresState(dsn)
        state.grant("syn-tenant", "syn-self", "101")
        state.grant("syn-tenant", "syn-clinician", "101", role="clinician")
        state.grant("syn-tenant", "syn-operator", "101", role="operator")
        build = subprocess.run(
            [
                "dotnet",
                "build",
                "preproduction/patient-tools/PatientTools.csproj",
                "--artifacts-path",
                str(out / "dotnet-artifacts"),
                "-p:BackendRoot=" + str(args.backend_repo),
                "--nologo",
                "--verbosity",
                "quiet",
            ],
            cwd=args.meta_repo,
            capture_output=True,
            timeout=120,
        )
        (out / "dotnet-build.log").write_bytes(build.stdout + build.stderr)
        if build.returncode:
            raise RuntimeError(".NET build failed; retained diagnostics")
        dll = out / "dotnet-artifacts/bin/PatientTools/debug/PatientTools.dll"
        dotnet = ["dotnet", str(dll), "--urls", urls["patient"]]
        for role in ["authority", "knowledge"]:
            start(role)
            await ready(role)
        start("patient", dotnet)
        await ready("patient")
        start("agent")
        await ready("agent")

        status, issued = await login()
        token = issued["authorization"]
        claims = decode(token)["claims"]
        record(
            "A01", status == 200 and claims["actor"] == "syn-self" and claims["role"] == "patient"
        )
        status, doctor = await login("clinician", "syn-doctor-session")
        doctor_token = doctor["authorization"]
        record("A02", status == 200 and decode(doctor_token)["claims"]["role"] == "clinician")
        for case, actor in [("A03", "operator"), ("A04", "other"), ("A05", "tenant")]:
            status, _ = await login(actor)
            record(case, status == 403, http_status=status)
        status, _ = await login(role="clinician")
        record("A06", status == 422, http_status=status)
        bad = delegate(token, issuer="wrong-authority")
        status, _ = await patient(bad)
        record("A07", status == 401, http_status=status)
        status, _ = await patient(token)
        record("A08", status == 401, http_status=status)
        value = decode(delegate(token))
        value["signature"] = "0" * 64
        status, _ = await patient(bearer(value))
        record("A09", status == 401, http_status=status)
        now = int(time.time())
        status, _ = await patient(delegate(token, issued_at=now - 121, expires_at=now - 1))
        record("A10", status == 401, http_status=status)
        status, _ = await patient(delegate(token), user="102")
        record("A11", status == 403, http_status=status)
        status, _ = await patient(delegate(token), session="wrong-session")
        record("A12", status == 403, http_status=status)
        status, profile = await patient(delegate(token))
        record(
            "A13",
            status == 200
            and profile["data"]["patient_ref"] == "project:101"
            and profile["contract_version"] == "patient-layers-1.0.0",
            http_status=status,
        )
        status, combined = await run(token, claims["session"])
        if status != 200:
            record("A14", False, http_status=status)
            raise RuntimeError("Positive native run failed; preserve this attempt")
        run_id = combined["run_id"]
        path = "/v1/synthetic-native/runs/" + run_id
        record(
            "A14",
            combined["patient"]["measurements"][0]["value"] == 0.42
            and combined["literature"]
            and combined["generated_answer"] is None,
            native_status=combined["native_status"],
        )
        capture = out / "model-inputs.jsonl"
        model_before = len(capture.read_bytes().splitlines())
        status, local = await run(token, claims["session"], "record")
        record(
            "A15",
            status == 200
            and local["patient"]
            and len(capture.read_bytes().splitlines()) == model_before,
            http_status=status,
        )
        record("A29", (await http("agent", path, doctor_token))[0] == 403)
        stop("agent")
        start("agent")
        await ready("agent")
        status, restored = await http("agent", path, token)
        record(
            "A16",
            status == 200 and restored == {k: v for k, v in combined.items() if k != "run_id"},
            process_restarted=True,
            http_status=status,
        )
        second = start("agent", name="second-agent")
        for _ in range(100):
            if second.poll() is not None:
                break
            await asyncio.sleep(0.1)
        record("A17", second.poll() is not None and second.returncode != 0)
        stop("second-agent")
        state.grant("syn-tenant", "syn-self", "101", active=False)
        record("A18", (await patient(delegate(token)))[0] == 403)
        record("A19", (await http("agent", path, token))[0] == 403)
        record(
            "A20",
            (
                await http(
                    "knowledge",
                    "/v1/preproduction/retrieve",
                    delegate(token, "knowledge"),
                    dict(concept="robotic_gait", patient_context="synthetic"),
                )
            )[0]
            == 403,
        )
        record(
            "A30",
            (await http("agent", path + "/citation?ref=syn-paper%40v1%23outcome", token))[0] == 403,
        )
        record("A35", (await http("agent", path + "/events", token))[0] == 403)
        stop("authority")
        start("authority")
        await ready("authority")
        record("A32", (await patient(delegate(token)))[0] == 403, process_restarted=True)
        state.grant("syn-tenant", "syn-self", "101")
        record("A21", (await patient(delegate(token)))[0] == 403)
        record("A22", (await login())[0] == 403)
        status, newer = await login(session="syn-session-b")
        new_token = newer["authorization"]
        status, newrun = await run(new_token, "syn-session-b")
        record("A23", status == 200 and newrun["patient"]["measurements"][0]["value"] == 0.42)
        newpath = "/v1/synthetic-native/runs/" + newrun["run_id"]
        record(
            "A26",
            (await http("agent", newpath, token))[0] == 403
            and (await http("agent", newpath, doctor_token))[0] == 403,
        )
        stop("patient")
        start("patient", dotnet)
        await ready("patient")
        record(
            "A36",
            (await patient(delegate(new_token), session="syn-session-b"))[0] == 200,
            process_restarted=True,
        )
        stop("authority")
        record("A24", (await patient(delegate(new_token), session="syn-session-b"))[0] == 503)
        record("A25", (await http("agent", newpath, new_token))[0] == 503)
        start("authority")
        await ready("authority")
        record("A34", (await patient(""))[0] == 401)
        messages = capture.read_bytes()
        record(
            "A31",
            all(
                c.encode() not in messages
                for c in [
                    "SYN_PRIVATE_NAME",
                    "+86-000",
                    "PRIVATE_HISTORY_CANARY",
                    "0.42",
                    "project:101",
                    "opaque-synthetic-101",
                ]
            ),
            captured_model_calls=len(messages.splitlines()),
            supplier_requests=0,
        )
        before = json.loads((ROOT / ".local/shared_authority_v3/predecessor.json").read_bytes())
        linked = [
            "AI_WebApi/Controllers/PatientLayerToolsController.cs",
            "AI_WebApi/Controllers/IReGoToolsController.cs",
            "AI_WebApi/Security/DifySystemContextResolver.cs",
        ]
        record(
            "A33",
            all(
                hashlib.sha256((args.backend_repo / n).read_bytes()).hexdigest()
                == before["backend"]["hashes"][n]
                for n in linked
            ),
            linked_sources=len(linked),
        )

        with ThreadPoolExecutor(max_workers=8) as pool:
            generations = list(
                pool.map(lambda _: state.grant("syn-race", "syn-race", "999"), range(16))
            )
        record(
            "A37",
            sorted(generations) == list(range(1, 17)),
            committed_generations=sorted(generations),
        )
        from meta_agent.preprod_security import Rejected, Subject

        def bind(patient):
            try:
                state.bind_session(
                    Subject.model_validate(
                        {
                            **claims,
                            "actor": "syn-race",
                            "session": "syn-race-session",
                            "patient": patient,
                        }
                    )
                )
                return True
            except Rejected:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            bound = list(pool.map(bind, ["201", "202"]))
        record("A38", sorted(bound) == [False, True], successful_bindings=sum(bound))

        stop("patient")
        env["SYNTHETIC_PROFILE_MARKER"] = str(out / "profile-entered.txt")
        env["SYNTHETIC_PROFILE_DELAY_MS"] = "1500"
        start("patient", dotnet)
        await ready("patient")
        in_flight = asyncio.create_task(patient(delegate(new_token), session="syn-session-b"))
        marker = out / "profile-entered.txt"
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.02)
        state.grant("syn-tenant", "syn-self", "101", active=False)
        delayed_status, _ = await in_flight
        record(
            "A40",
            marker.exists() and delayed_status == 403,
            controller_entered=marker.exists(),
            final_http_status=delayed_status,
        )

        stop("agent")
        from meta_agent.config import Settings
        from meta_agent.infrastructure.container import create_container

        fixture = await create_container(
            Settings(
                _env_file=None,
                app_env="test",
                dry_run=True,
                orchestration_mode="legacy",
                planner_mode="deterministic",
                persistence_backend="postgres",
                postgres_dsn=config["agent_dsn"],
                auto_setup_persistence=True,
            )
        )
        try:
            scope = state.get("native_run", newrun["run_id"])["native_scope"]
            local_wrapper = state.get("native_run", local["run_id"])
            orphan_thread = local_wrapper["native_conversation"]
            orphan_before = await fixture.repository.store.aget(
                fixture.repository.namespace("conversations", orphan_thread), "state"
            )
            await fixture.repository.delete("runs", local_wrapper["native_scope"], local["run_id"])
            for i in range(151):
                await fixture.repository.put(
                    "evidence", scope, f"pagination-{i}", {"synthetic": True}, 600
                )
            await fixture.repository.put(
                "cache", "syn-live-neighbor", "neighbor-keep", {"synthetic_neighbor": True}, 600
            )
        finally:
            await fixture.close()
        # Explicit expiry fault injection in our disposable DB, not shortened production TTL.
        with state.connect() as db:
            wrappers = db.execute(
                "SELECT key,value FROM synthetic_kv WHERE kind='native_run'"
            ).fetchall()
            db.execute(
                "UPDATE synthetic_kv SET value=jsonb_set(value,'{expires_at}', '0') "
                "WHERE kind='native_run'"
            )
        with psycopg.connect(config["agent_dsn"]) as db:
            tables = [
                r[0]
                for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            ]
            counts_before = {
                t: db.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM {}").format(psycopg.sql.Identifier(t))
                ).fetchone()[0]
                for t in tables
                if t in {"store", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
            }
        cleanup = start("cleanup")
        for _ in range(200):
            if cleanup.poll() is not None:
                break
            await asyncio.sleep(0.1)
        if cleanup.poll() is None or cleanup.returncode:
            raise RuntimeError("Cleanup failed; retained diagnostics")
        stop("cleanup")
        with psycopg.connect(config["agent_dsn"]) as db:
            counts_after = {
                t: db.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM {}").format(psycopg.sql.Identifier(t))
                ).fetchone()[0]
                for t in counts_before
            }
        with state.connect() as db:
            wrappers_after = db.execute(
                "SELECT count(*) FROM synthetic_kv WHERE kind='native_run'"
            ).fetchone()[0]
        record(
            "A27",
            len(wrappers) >= 3
            and counts_before.get("store", 0) > 0
            and counts_after.get("store") == 1
            and wrappers_after == 0,
            physical_rows_before=counts_before,
            physical_rows_after=counts_after,
            wrappers_after=wrappers_after,
        )
        record(
            "A28",
            counts_before.get("checkpoints", 0) > 0
            and all(
                counts_after.get(t) == 0
                for t in ["checkpoints", "checkpoint_blobs", "checkpoint_writes"]
            ),
            physical_rows_before=counts_before,
            physical_rows_after=counts_after,
        )
        again = start("cleanup")
        for _ in range(200):
            if again.poll() is not None:
                break
            await asyncio.sleep(0.1)
        with psycopg.connect(config["agent_dsn"]) as db:
            kept = db.execute("SELECT key,value FROM store").fetchall()
        record(
            "A39",
            again.poll() == 0
            and len(kept) == 1
            and kept[0][0] == "neighbor-keep"
            and kept[0][1]["value"] == {"synthetic_neighbor": True},
            pagination_rows=151,
            retained_live_rows=len(kept),
            second_cleanup_exit=again.poll(),
        )
        stop("cleanup")
        record(
            "A41",
            orphan_before is not None and len(kept) == 1 and kept[0][0] == "neighbor-keep",
            prior_run_deleted=True,
            orphan_conversation_existed=orphan_before is not None,
            retained_live_rows=len(kept),
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        (out / "runner-error.log").write_text(traceback.format_exc(), encoding="utf8")
        print(dict(error_type=type(exc).__name__, details="private runner log"), flush=True)
    finally:
        for process in list(processes):
            stop(process)
        for handle in files:
            handle.close()
        if cid:
            removed = subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30)
            result["cleanup_complete"] = removed.returncode == 0
        result["missing_cases"] = sorted(set(cases) - {r["id"] for r in result["cases"]})
        result["passed"] = sum(r["passed"] for r in result["cases"])
        result["status"] = (
            "PASS" if result["passed"] == len(cases) and result.get("cleanup_complete") else "FAIL"
        )
        checkpoint()
    print(
        dict(
            status=result["status"],
            passed=result["passed"],
            total=len(cases),
            cleanup_complete=result.get("cleanup_complete"),
        ),
        flush=True,
    )
    return result["status"] == "PASS"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--backend-repo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    options = p.parse_args()
    if sys.platform == "win32":
        okay = asyncio.run(main(options), loop_factory=asyncio.SelectorEventLoop)
    else:
        okay = asyncio.run(main(options))
    raise SystemExit(0 if okay else 1)
