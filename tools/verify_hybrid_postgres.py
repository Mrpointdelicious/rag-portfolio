"""Allocate an owned ephemeral PostgreSQL container; use existing isolated DB runner only."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def main(args):
    if args.output.exists():
        raise SystemExit("Use a new output directory")
    args.output.mkdir(parents=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    name = "rag-hybrid-test-" + uuid4().hex[:12]
    password = secrets.token_hex(24)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "RAG_ENVIRONMENT": "test",
        "RAG_POSTGRES_HOST": "127.0.0.1",
        "RAG_POSTGRES_PORT": str(port),
        "RAG_POSTGRES_USER": "rag_portfolio",
        "RAG_POSTGRES_PASSWORD": password,
        "RAG_POSTGRES_DATABASE": "hybrid_test_admin",
    }
    cid = None
    report = {
        "execution": "real owned ephemeral PostgreSQL; no existing DB contacted",
        "port": port,
        "steps": [],
    }

    def save():
        (args.output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf8")

    def run(label, cmd, cwd, timeout=180):
        r = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf8",
            errors="replace",
            timeout=timeout,
        )
        (args.output / (label + ".log")).write_text(r.stdout, encoding="utf8")
        report["steps"].append({"step": label, "exit": r.returncode})
        save()
        print({"step": label, "exit": r.returncode}, flush=True)
        return r

    try:
        r = run(
            "create",
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "codex.hybrid.acceptance=owned",
                "-p",
                f"127.0.0.1:{port}:5432",
                "--tmpfs",
                "/var/lib/postgresql/data:rw",
                "-e",
                "POSTGRES_USER=rag_portfolio",
                "-e",
                "POSTGRES_PASSWORD=" + password,
                "-e",
                "POSTGRES_DB=hybrid_test_admin",
                args.image,
            ],
            ROOT,
        )
        if r.returncode:
            raise RuntimeError("Could not create owned test PostgreSQL")
        cid = r.stdout.strip().splitlines()[-1]
        dsn = f"postgresql://rag_portfolio:{password}@127.0.0.1:{port}/hybrid_test_admin"
        for _attempt in range(60):
            try:
                with psycopg.connect(dsn, connect_timeout=1, autocommit=True) as conn:
                    conn.execute("CREATE DATABASE metaagent_test")
                break
            except psycopg.OperationalError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Owned test DB did not become ready")
        run(
            "rag-isolated-suite",
            [str(ROOT / ".venv/Scripts/python.exe"), "tools/test.py", "-ra"],
            ROOT,
        )
        if args.meta_repo:
            env["META_AGENT_TEST_POSTGRES_DSN"] = dsn.rsplit("/", 1)[0] + "/metaagent_test"
            run(
                "meta-isolated-suite",
                [str(args.meta_repo / ".venv/Scripts/python.exe"), "-m", "pytest", "-ra"],
                args.meta_repo,
            )
    finally:
        if cid:
            run("remove-owned-container", ["docker", "rm", "-f", cid], ROOT, timeout=30)
        report["cleanup_complete"] = (
            cid is not None
            and not subprocess.check_output(
                ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"], text=True
            ).strip()
        )
        save()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--meta-repo", type=Path)
    p.add_argument("--image", default="postgres:15-alpine")
    main(p.parse_args())
