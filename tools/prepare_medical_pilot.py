"""Prepare an owned local pilot bundle; never copy existing model credentials."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path

from rag_portfolio.medical_release import ReleaseRegistry

ROOT = Path(__file__).resolve().parents[1]


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(args):
    directory = ROOT / ".local/medical_business_v2/pilot"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bundle").mkdir(exist_ok=True)
    (directory / "logs").mkdir(exist_ok=True)
    source = ReleaseRegistry(ROOT / ".local/medical_business_v1/releases/registry.json")
    state = source.read()
    mapping = {"public-baseline": "public", "combined-business-v1": "combined"}
    releases = {}
    for rid, prefix in mapping.items():
        e = state["releases"][rid].copy()
        source.bundle(e)
        for kind in ["corpus", "index", "approval"]:
            key = kind + "_path"
            if e[key]:
                dest = directory / "bundle" / f"{prefix}-{kind}.json"
                if not dest.exists():
                    shutil.copyfile(e[key], dest)
                if (
                    hashlib.sha256(dest.read_bytes()).digest()
                    != hashlib.sha256(Path(e[key]).read_bytes()).digest()
                ):
                    raise ValueError("Bundle drift")
                e[key] = f"/data/{dest.name}"
        releases[rid] = e
    active = "combined-business-v1" if args.release == "combined" else "public-baseline"
    registry = directory / "registry.json"
    previous = (
        json.loads(registry.read_text()) if registry.exists() else {"generation": 0, "history": []}
    )
    for rid, entry in previous.get("releases", {}).items():
        if entry.get("status") == "revoked" and rid in releases:
            releases[rid]["status"] = "revoked"
    if releases[active]["status"] == "revoked":
        raise ValueError("Revocation is irreversible; register a new release ID")
    new = {
        "schema_version": "medical-release-registry-v1",
        "active": active,
        "releases": releases,
        "generation": previous["generation"] + 1,
        "history": previous["history"]
        + [{"action": "explicit_operator_pilot_select", "release_id": active}],
    }
    atomic_json(registry, new)
    config_path = directory / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        pg = secrets.token_hex(32)
        operators = {n: secrets.token_urlsafe(24) for n in ["reviewer-a", "reviewer-b"]}
        (directory / "pg-password.txt").write_text(pg, encoding="ascii")
        (directory / "operator-access.json").write_text(json.dumps(operators), encoding="utf8")
        config = dict(
            rag_token=secrets.token_urlsafe(40),
            agent_token=secrets.token_urlsafe(40),
            postgres_dsn=f"postgresql://pilot:{pg}@db:5432/pilot",
            operators={n: hashlib.sha256(p.encode()).hexdigest() for n, p in operators.items()},
            clinical_approval=False,
            scope_key="medical-business-v1",
            registry_path="/run/pilot/registry.json",
        )
    config.update(
        {k: releases[active][k] for k in ["corpus_version", "corpus_sha256", "index_sha256"]},
        stage=args.stage,
        prompt_bundle="business-grounded-v10" if args.stage == "C" else "business-grounded-v9",
        allowed_origins=[
            f"http://127.0.0.1:{args.agent_port}",
            f"http://localhost:{args.agent_port}",
        ],
    )
    atomic_json(config_path, config)
    manifest = {
        k: config[k] for k in ["corpus_version", "corpus_sha256", "index_sha256", "scope_key"]
    }
    manifest.update(
        loaded_domains=["health"],
        source_kind="published_medical_literature",
        clinical_approval=False,
    )
    (directory / "release-capabilities.json").write_text(json.dumps(manifest), encoding="utf8")
    env = dict(
        PILOT_DIR=directory.as_posix(),
        RAG_REPO=ROOT.as_posix(),
        META_REPO=args.meta_repo.resolve().as_posix(),
        RAG_PORT=args.rag_port,
        AGENT_PORT=args.agent_port,
    )
    (directory / "compose.env").write_text(
        "".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf8"
    )
    print(
        json.dumps(
            dict(
                prepared=str(directory),
                release=active,
                stage=args.stage,
                existing_credentials_copied=False,
            )
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--release", choices=["combined", "public"], default="combined")
    p.add_argument("--stage", choices=["A", "B", "C"], default="C")
    p.add_argument("--rag-port", type=int, default=18120)
    p.add_argument("--agent-port", type=int, default=18121)
    main(p.parse_args())
