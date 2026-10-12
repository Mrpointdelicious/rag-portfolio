"""Exercise project identity against actual MySQL DDL and owned native/.NET processes."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import psycopg
import pymysql

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

    out = args.output.resolve()
    if out.exists():
        raise ValueError("Use a new directory; preserve all attempts")
    out.mkdir(parents=True)
    sealed = ROOT / "eval/project_identity_v4/cases.jsonl"
    seal = json.loads(sealed.with_name("seal.json").read_bytes())
    assert hashlib.sha256(sealed.read_bytes()).hexdigest() == seal["cases_sha256"]
    schema = sealed.with_name("schema.sql")
    schema_seal = json.loads(schema.with_name("schema-seal.json").read_bytes())
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == schema_seal["schema_sha256"]
    cases = {r["id"]: r for r in map(json.loads, sealed.read_bytes().splitlines())}
    addendum = sealed.with_name("addendum-cases.jsonl")
    addendum_seal = json.loads(addendum.with_name("addendum-seal.json").read_bytes())
    assert hashlib.sha256(addendum.read_bytes()).hexdigest() == addendum_seal["cases_sha256"]
    cases.update({r["id"]: r for r in map(json.loads, addendum.read_bytes().splitlines())})
    result = dict(
        cases=[],
        synthetic_only=True,
        clinical_approval=False,
        actual_supplier_requests=0,
        source_data_rows_copied=0,
        schema_sha256=schema_seal["schema_sha256"],
        process_starts=[],
    )
    containers, processes, logs, private = [], {}, [], []
    ports = {r: free_port() for r in ["authority", "agent", "patient", "knowledge", "pg", "mysql"]}
    urls = {r: f"http://127.0.0.1:{p}" for r, p in ports.items()}
    password, root_password, reader_password = [secrets.token_urlsafe(30) for _ in range(3)]
    keys = {r: secrets.token_urlsafe(40) for r in ["agent", "patient-tools", "knowledge"]}
    services = {r: secrets.token_urlsafe(40) for r in keys}
    jwt_config = dict(
        issuer="owned-project-fixture",
        audience="owned-project-agent",
        key=secrets.token_urlsafe(40),
    )
    config = dict(
        authority_dsn=f"postgresql://synthetic:{password}@127.0.0.1:{ports['pg']}/"
        "synthetic_authority_v3",
        agent_dsn=f"postgresql://synthetic:{password}@127.0.0.1:{ports['pg']}/metaagent_test",
        keys=keys,
        service_tokens=services,
        response_key=secrets.token_urlsafe(40),
        authority_url=urls["authority"],
        patient_url=urls["patient"],
        knowledge_url=urls["knowledge"],
        patient_refs=[
            ["syn-tenant", "101", "opaque-synthetic-101"],
            ["syn-tenant", "102", "opaque-synthetic-102"],
        ],
        expected_pins=dict(corpus_sha256="c" * 64, index_sha256="i" * 64),
        project_identity=dict(
            jwt=jwt_config,
            tenants={"syn-tenant": 10, "syn-other": 20},
            database=dict(
                host="127.0.0.1",
                port=ports["mysql"],
                database="synthetic_project_v4",
                user="project_reader",
                password=reader_password,
            ),
        ),
    )
    config_path = out / "owned-config.json"
    save(config_path, config)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "SYNTHETIC_ONLY": "1",
        "AUTHORITY_URL": urls["authority"],
        "AUTHORITY_SERVICE_TOKEN": services["patient-tools"],
        "PYTHONPATH": os.pathsep.join([str(args.meta_repo / "src"), str(ROOT / "src")]),
    }

    def checkpoint():
        save(out / "result.json", result)
        save(out / "private-http-review.json", private)

    def record(cid, ok, **evidence):
        assert cid not in {r["id"] for r in result["cases"]}
        result["cases"].append(dict(**cases[cid], passed=bool(ok), evidence=evidence))
        checkpoint()
        print(dict(case=cid, passed=bool(ok)), flush=True)

    def docker(arguments):
        r = subprocess.run(["docker", *arguments], capture_output=True, timeout=40)
        if r.returncode:
            raise RuntimeError("Owned container operation failed")
        return r.stdout.decode().strip()

    def admin():
        return pymysql.connect(
            host="127.0.0.1",
            port=ports["mysql"],
            user="root",
            password=root_password,
            database="synthetic_project_v4",
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=2,
        )

    def sql(query, params=()):
        with admin() as db, db.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def reset():
        with admin() as db, db.cursor() as cur:
            cur.execute("SET SESSION sql_mode=CONCAT(@@sql_mode,',NO_AUTO_VALUE_ON_ZERO')")
            for table in [
                "dbmedical",
                "dbrehaplan",
                "dbroleuser",
                "dbbuniessuser",
                "dbuser",
                "dbrole",
                "dbbuniess",
            ]:
                cur.execute("DELETE FROM " + table)
            cur.execute("INSERT INTO dbbuniess(Id,Status,IsDeleted) VALUES (10,0,0),(20,0,0)")
            cur.execute(
                "INSERT INTO dbrole(Id,Name,Enable,IsDeleted,TenantId) VALUES "
                "(0,'Patient',0,0,0),(1,'Doctor',0,0,0),(5,'Admin',0,0,10)"
            )
            for uid, business, status, role in [
                (101, 10, 1, 0),
                (102, 10, 1, 0),
                (201, 10, 1, 1),
                (202, 10, 1, 1),
                (301, 10, 1, 5),
                (401, 20, 1, 0),
            ]:
                cur.execute(
                    "INSERT INTO dbuser(Id,BusinessId,Status,IsDeleted) VALUES (%s,%s,%s,b'0')",
                    (uid, business, status),
                )
                cur.execute(
                    "INSERT INTO dbbuniessuser(Id,UserId,BusinessId,Enable) VALUES (%s,%s,%s,0)",
                    (uid, uid, business),
                )
                cur.execute(
                    "INSERT INTO dbroleuser(Id,UserId,BusinessId,RoleId,Enable) "
                    "VALUES (%s,%s,%s,%s,0)",
                    (uid, uid, business, role),
                )
            cur.execute(
                "INSERT INTO dbmedical(Id,UserId,DoctorId,TenantId,Status,Enable,"
                "CreateTime,UpdateTime) VALUES(501,101,201,10,0,b'1',NOW(),NOW())"
            )
            cur.execute(
                "INSERT INTO dbrehaplan(Id,UserId,DoctorId,BusinessId,IsComplete,"
                "BookingTime) VALUES(601,101,202,10,0,NOW())"
            )

    def token(uid="201", **updates):
        now = int(time.time())
        payload = dict(
            iss=jwt_config["issuer"],
            aud=jwt_config["audience"],
            jti=uid,
            UserId=uid,
            iat="2026/10/12 12:00:00",
            nbf=now - 1,
            exp=now + 600,
            Role="Admin",
            UserData="999,10,20",
        )
        payload.update(updates)
        return "Bearer " + jwt.encode(payload, jwt_config["key"], algorithm="HS256")

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

    async def login(session, header=None, **updates):
        body = dict(tenant="syn-tenant", patient="101", session=session)
        body.update(updates)
        return await http("authority", "/sessions", token() if header is None else header, body)

    async def introspect(header):
        return await http(
            "authority", "/introspect", "Bearer " + services["agent"], dict(authorization=header)
        )

    def delegate(header):
        claims = {**decode(header)["claims"], "audience": "patient-tools"}
        return bearer(sign(claims, keys["patient-tools"]))

    async def patient(header, session):
        return await http(
            "patient",
            "/api/ai/patients/tools/get_patient_profile",
            delegate(header),
            dict(system_context=dict(user="101", conversation_id=session)),
        )

    def start(role, command=None):
        log = (out / f"{role}-{len(result['process_starts'])}.log").open("wb")
        logs.append(log)
        if command is None:
            command = [
                str(args.meta_repo / ".venv/Scripts/python.exe"),
                "-X",
                "utf8",
                str(args.meta_repo / "tools/shared_authority_worker.py"),
                "--config",
                str(config_path),
                "--role",
                role,
                "--port",
                str(ports[role]),
            ]
        p = subprocess.Popen(
            command,
            cwd=args.meta_repo,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        processes[role] = p
        result["process_starts"].append(dict(role=role, pid=p.pid))

    def stop(role):
        p = processes.pop(role, None)
        if p and p.poll() is None:
            p.kill()
            p.wait(timeout=15)

    async def ready(role):
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if processes[role].poll() is not None:
                    raise RuntimeError("Owned worker exited; inspect private log")
                try:
                    if (await client.get(urls[role] + "/health")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
        raise RuntimeError("Owned worker readiness timeout")

    try:
        cid_pg = docker(
            [
                "run",
                "-d",
                "--name",
                "project-v4-pg-" + uuid4().hex[:12],
                "--label",
                "codex.project.v4=owned",
                "-p",
                f"127.0.0.1:{ports['pg']}:5432",
                "--tmpfs",
                "/var/lib/postgresql/data:rw",
                "-e",
                "POSTGRES_USER=synthetic",
                "-e",
                "POSTGRES_PASSWORD=" + password,
                "-e",
                "POSTGRES_DB=synthetic_authority_v3",
                "postgres:15-alpine",
            ]
        )
        containers.append(cid_pg)
        cid_mysql = docker(
            [
                "run",
                "-d",
                "--name",
                "project-v4-mysql-" + uuid4().hex[:12],
                "--label",
                "codex.project.v4=owned",
                "-p",
                f"127.0.0.1:{ports['mysql']}:3306",
                "--tmpfs",
                "/var/lib/mysql:rw",
                "-e",
                "MYSQL_ROOT_PASSWORD=" + root_password,
                "-e",
                "MYSQL_ROOT_HOST=%",
                "-e",
                "MYSQL_DATABASE=synthetic_project_v4",
                "mysql:8.0",
                "--default-authentication-plugin=mysql_native_password",
                "--default-time-zone=+08:00",
            ]
        )
        containers.append(cid_mysql)
        for _ in range(100):
            try:
                with psycopg.connect(
                    config["authority_dsn"], autocommit=True, connect_timeout=1
                ) as db:
                    db.execute("CREATE DATABASE metaagent_test")
                break
            except psycopg.OperationalError:
                await asyncio.sleep(0.3)
        else:
            raise RuntimeError("Owned PostgreSQL unavailable")
        for _ in range(100):
            try:
                with admin() as db:
                    pass
                break
            except pymysql.MySQLError:
                await asyncio.sleep(0.4)
        else:
            raise RuntimeError("Owned MySQL unavailable")
        with admin() as db, db.cursor() as cur:
            # Preserve the exported legacy COMPACT DDL. MySQL 8 strict InnoDB
            # DDL validation otherwise rejects its wide nullable metadata rows.
            # Only this owned schema-creation session changes that setting.
            cur.execute("SET SESSION innodb_strict_mode=OFF")
            for ddl in re.findall(r"CREATE TABLE .*?\) ENGINE[^;]*;", schema.read_text(), re.S):
                cur.execute(ddl)
            cur.execute("CREATE USER %s@'%%' IDENTIFIED BY %s", ("project_reader", reader_password))
            columns = {
                "dbbuniess": "Id,Status,IsDeleted",
                "dbuser": "Id,Status,IsDeleted,BusinessId",
                "dbbuniessuser": "Id,UserId,BusinessId,Enable",
                "dbroleuser": "Id,UserId,BusinessId,RoleId,Enable",
                "dbrole": "Id,Name,IsDeleted,Enable,TenantId",
                "dbmedical": "Id,UserId,DoctorId,TenantId,Status,Enable,CreateTime,UpdateTime",
            }
            for table, allowed in columns.items():
                cur.execute(
                    f"GRANT SELECT ({allowed}) ON synthetic_project_v4.{table} "
                    "TO 'project_reader'@'%'"
                )
        reset()
        start("authority")
        await ready("authority")
        for i in range(1, 41):
            cid = f"P{i:02d}"
            reset()
            head, body, expected = token(), {}, 403
            if i == 1:
                head, expected = token("101"), 200
            elif i == 2:
                expected = 200
            elif i == 3:
                head, expected = "", 401
            elif i == 4:
                head, expected = (
                    "Bearer "
                    + jwt.encode(
                        jwt.decode(token()[7:], options={"verify_signature": False}),
                        secrets.token_urlsafe(40),
                        algorithm="HS256",
                    ),
                    401,
                )
            elif i == 5:
                head, expected = (
                    "Bearer "
                    + jwt.encode(
                        jwt.decode(token()[7:], options={"verify_signature": False}),
                        "",
                        algorithm="none",
                    ),
                    401,
                )
            elif i in [6, 7, 8, 9, 10, 11, 12]:
                overrides = {
                    6: dict(iss="wrong"),
                    7: dict(aud="wrong"),
                    8: dict(exp=int(time.time()) - 1),
                    9: dict(nbf=int(time.time()) + 300),
                    10: dict(UserId=["201", "202"]),
                    11: dict(UserId="202"),
                    12: dict(jti="0", UserId="0"),
                }
                head, expected = token(**overrides[i]), 401
            elif i == 13:
                head = token("301", Role="Doctor")
            elif i == 14:
                body, expected = dict(actor="project:201", role="clinician"), 422
            elif i == 15:
                body = dict(tenant="unknown")
            elif i == 16:
                body = dict(tenant="syn-other")
            elif i in [17, 36]:
                head = token("202")
                if i == 17:
                    sql("DELETE FROM dbrehaplan WHERE DoctorId=202")
            elif i == 18:
                head, body = token("101"), dict(patient="102")
            elif i == 19:
                head = token("301")
            elif i == 20:
                sql("UPDATE dbuser SET IsDeleted=b'1' WHERE Id=201")
            elif i == 21:
                sql("UPDATE dbuser SET Status=9 WHERE Id=201")
            elif i == 22:
                sql("UPDATE dbbuniess SET Status=1 WHERE Id=10")
            elif i == 23:
                sql("UPDATE dbbuniess SET IsDeleted=2 WHERE Id=10")
            elif i == 24:
                sql("DELETE FROM dbbuniessuser WHERE UserId=201")
            elif i == 25:
                sql("UPDATE dbbuniessuser SET Enable=1 WHERE UserId=101")
            elif i == 26:
                sql("UPDATE dbroleuser SET BusinessId=20 WHERE UserId=201")
            elif i == 27:
                sql("UPDATE dbroleuser SET Enable=1 WHERE UserId=201")
            elif i == 28:
                sql("UPDATE dbrole SET IsDeleted=1 WHERE Id=1")
            elif i == 29:
                sql("UPDATE dbrole SET Enable=-1 WHERE Id=1")
            elif i == 30:
                sql("UPDATE dbmedical SET Status=1")
            elif i == 31:
                sql("UPDATE dbmedical SET Enable=b'0'")
            elif i == 32:
                sql("UPDATE dbmedical SET TenantId=20")
            elif i == 33:
                sql("UPDATE dbmedical SET CreateTime=NOW()-INTERVAL 8 DAY")
            elif i == 34:
                sql("UPDATE dbmedical SET CreateTime=NOW()+INTERVAL 1 DAY")
            elif i == 35:
                sql("UPDATE dbmedical SET CreateTime='1900-01-01 00:00:00'")
            elif i == 37:
                sql("INSERT INTO dbbuniessuser(Id,UserId,BusinessId,Enable) VALUES(999,201,10,0)")
            elif i == 38:
                sql(
                    "INSERT INTO dbroleuser(Id,UserId,BusinessId,RoleId,Enable) "
                    "VALUES(999,201,10,1,0)"
                )
            elif i in [39, 40]:
                from meta_agent.project_identity import ProjectDatabase, ProjectPolicy

                sql("UPDATE dbuser SET OldId='777' WHERE Id=101")
                policy = ProjectPolicy(
                    ProjectDatabase(**config["project_identity"]["database"]),
                    config["project_identity"]["tenants"],
                )
                decision = policy.decide("syn-tenant", 201, "101")
                if i == 39:
                    binding = policy.binding(decision)
                    record(
                        cid,
                        binding["project_patient_id"] == 101
                        and binding["data_generation"] == "project-20260915",
                    )
                else:
                    from meta_agent.preprod_security import Rejected

                    try:
                        policy.binding(decision, "robotdb")
                        record(cid, False)
                    except Rejected as exc:
                        record(cid, exc.code == "legacy_identity_unavailable")
                continue
            status, value = await login(cid, head, **body)
            ok = status == expected
            if expected == 200:
                claims = decode(value["authorization"])["claims"]
                ok = ok and claims["role"] == ("patient" if i == 1 else "clinician")
                ok = ok and claims["actor"] == ("project:101" if i == 1 else "project:201")
            record(cid, ok, http_status=status)
        reset()
        denied = []
        with pymysql.connect(**config["project_identity"]["database"], connect_timeout=2) as db:
            for query in ["UPDATE dbuser SET Status=9 WHERE Id=101", "SELECT Password FROM dbuser"]:
                try:
                    with db.cursor() as cur:
                        cur.execute(query)
                    denied.append(False)
                except pymysql.MySQLError:
                    denied.append(True)
        record("P41", all(denied), write_denied=denied[0], password_read_denied=denied[1])
        _, value = await login("revoke-session")
        old = value["authorization"]
        sql("UPDATE dbmedical SET Status=1 WHERE Id=501")
        status, _ = await introspect(old)
        record("P42", status == 403, http_status=status)
        sql("UPDATE dbmedical SET Status=0 WHERE Id=501")
        status, _ = await introspect(old)
        record("P43", status == 403, http_status=status)
        status, value = await login("new-session")
        current = value.get("authorization", "")
        record("P44", status == 200 and (await introspect(current))[0] == 200)
        status, _ = await login("revoke-session")
        record("P45", status == 403, http_status=status)
        docker(["pause", cid_mysql])
        try:
            status, value = await introspect(current)
            record("P46", status == 503 and value == {"code": "project_authorization_unavailable"})
        finally:
            docker(["unpause", cid_mysql])
        stop("authority")
        start("authority")
        await ready("authority")
        record("P47", (await introspect(old))[0] == 403 and (await introspect(current))[0] == 200)
        build = await asyncio.to_thread(
            subprocess.run,
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
            raise RuntimeError("Linked dotnet build failed")
        start(
            "patient",
            [
                "dotnet",
                str(out / "dotnet-artifacts/bin/PatientTools/debug/PatientTools.dll"),
                "--urls",
                urls["patient"],
            ],
        )
        await ready("patient")
        start("agent")
        await ready("agent")
        status, value = await login("native-session")
        native_token = value["authorization"]
        status, native = await http(
            "agent",
            "/v1/synthetic-native/runs",
            native_token,
            dict(session_id="native-session", task="record", concept=None),
        )
        capture = out / "model-inputs.jsonl"
        record(
            "P48",
            status == 200
            and native.get("native_status") in {"succeeded", "partial"}
            and bool(native.get("patient"))
            and not capture.exists(),
            http_status=status,
            model_calls=0 if not capture.exists() else len(capture.read_bytes().splitlines()),
            backend_service="synthetic_fixture",
            controller_source="actual_linked_source",
        )
        if status != 200:
            raise RuntimeError("Native positive acceptance failed")
        path = "/v1/synthetic-native/runs/" + native["run_id"]
        sql("UPDATE dbmedical SET Status=1 WHERE Id=501")
        statuses = [
            (await http("agent", path, native_token))[0],
            (await http("agent", path + "/events", native_token))[0],
            (await patient(native_token, "native-session"))[0],
        ]
        record("P49", statuses == [403, 403, 403], http_statuses=statuses)
        sql("UPDATE dbmedical SET Status=0 WHERE Id=501")
        _, value = await login("route-session")
        delegated = delegate(value["authorization"])
        statuses = []
        for route in ["resolve_patient_identity", "get_patient_consultation", "get_patient_rehab"]:
            statuses.append(
                (
                    await http(
                        "patient",
                        "/api/ai/patients/tools/" + route,
                        delegated,
                        dict(system_context=dict(user="101", conversation_id="route-session")),
                    )
                )[0]
            )
        statuses.append(
            (await http("patient", "/api/ai/patients/101/rehab-overview", delegated))[0]
        )
        record("P50", all(s in [403, 404] for s in statuses), http_statuses=statuses)
        for number in range(52, 57):
            reset()
            expected = 403
            login_header = token()
            if number == 52:
                sql("UPDATE dbuser SET Status=0 WHERE Id=201")
            elif number == 53:
                sql("UPDATE dbuser SET Status=0 WHERE Id=101")
            elif number == 54:
                sql("UPDATE dbuser SET BusinessId=20 WHERE Id=201")
            elif number == 55:
                expected = 200
                parent_expiry = int(time.time()) + 45
                login_header = token(exp=parent_expiry)
            else:
                sql("DELETE FROM dbrole WHERE Id=1")
                sql(
                    "INSERT INTO dbrole(Id,Name,Enable,IsDeleted,TenantId) "
                    "VALUES(9,'Doctor',0,0,20)"
                )
                sql("UPDATE dbroleuser SET RoleId=9 WHERE UserId=201")
            status, value = await login(f"P{number}", login_header)
            ok = status == expected
            if number == 55 and ok:
                ok = decode(value["authorization"])["claims"]["expires_at"] <= parent_expiry
            record(f"P{number}", ok, http_status=status)
        before = json.loads((ROOT / ".local/project_identity_v4/predecessor.json").read_bytes())
        protected = before["backend"]
        untouched = all(
            hashlib.sha256((Path(protected["root"]) / p).read_bytes()).hexdigest() == h
            for p, h in protected["hashes"].items()
        )
        unchanged_sources = all(
            hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
            for p, h in before["sources"].items()
        )
        unchanged_sources &= all(
            hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
            for p, h in addendum_seal["sources"].items()
        )
        record(
            "P51",
            untouched and unchanged_sources,
            backend_files=len(protected["hashes"]),
            source_files=len(before["sources"]),
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        (out / "runner-error.log").write_text(traceback.format_exc(), encoding="utf8")
        print(dict(error_type=type(exc).__name__, details="retained private log"), flush=True)
    finally:
        for role in list(processes):
            stop(role)
        for handle in logs:
            handle.close()
        cleanup = []
        for container in containers:
            r = subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=40)
            cleanup.append(r.returncode == 0)
        result["cleanup_complete"] = len(cleanup) == 2 and all(cleanup)
        result["missing_cases"] = sorted(set(cases) - {r["id"] for r in result["cases"]})
        result["passed"] = sum(r["passed"] for r in result["cases"])
        result["status"] = (
            "PASS" if (result["passed"] == len(cases) and result["cleanup_complete"]) else "FAIL"
        )
        checkpoint()
    print(
        dict(
            status=result["status"],
            passed=result["passed"],
            total=len(cases),
            cleanup_complete=result["cleanup_complete"],
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
