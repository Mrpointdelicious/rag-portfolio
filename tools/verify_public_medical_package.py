"""Inspect real built packages and an owned Docker build-context snapshot; no push."""

import argparse
import json
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from rag_portfolio.public_medical import sha, write_json

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/public_medical_corpus_v1"


def forbidden(name):
    parts = name.replace("\\", "/").split("/")
    return (
        any(
            p in {".local", ".venv", ".git", "local_sources", "parsed", "indexes", "private_eval"}
            for p in parts
        )
        or ("doc" in parts and any(p in {"selected", "unselected"} for p in parts))
        or parts[-1] == ".env"
        or (parts[-1].startswith(".env.") and parts[-1] != ".env.example")
        or name.endswith((".pdf", ".xml"))
    )


def run(cmd, log):
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf8",
        errors="replace",
        timeout=180,
    )
    log.write_text(completed.stdout + completed.stderr, encoding="utf8")
    if completed.returncode:
        raise RuntimeError("Packaging command failed; see sanitized local log")
    return completed.stdout.strip()


def main(*, local=LOCAL, output=None):
    global LOCAL
    LOCAL = local
    output = output or ROOT / "artifacts/public-medical-corpus-v1/package_audit.json"
    (LOCAL / "verification").mkdir(parents=True, exist_ok=True)
    dist = LOCAL / "dist-final"
    dist.mkdir(exist_ok=True)
    run(["uv", "build", "--out-dir", str(dist)], LOCAL / "verification/package-build.log")
    packages = []
    for file in sorted(dist.iterdir()):
        if file.name.endswith(".tar.gz"):
            with tarfile.open(file) as archive:
                names = archive.getnames()
        elif file.suffix == ".whl":
            with zipfile.ZipFile(file) as archive:
                names = archive.namelist()
        else:
            continue
        packages.append(
            {
                "name": file.name,
                "sha256": sha(file.read_bytes()),
                "file_count": len(names),
                "forbidden": [n for n in names if forbidden(n)],
                "medical_modules": [
                    n for n in names if "public_medical" in n or "medical_release" in n
                ],
                "missing_release_modules": [
                    module
                    for module in ["medical_release.py", "medical_release_service.py"]
                    if not any(n.endswith("rag_portfolio/" + module) for n in names)
                ],
            }
        )
    tag = "codex-public-medical-context-" + uuid4().hex[:12]
    container = None
    docker = {
        "kind": "real COPY-context build and export inspection; "
        "no application container runtime claimed",
        "owned_tag": tag,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="public-medical-context-") as directory:
            dockerfile = Path(directory) / "Dockerfile"
            dockerfile.write_text("FROM scratch\nCOPY . /workspace\n", encoding="utf8")
            run(
                ["docker", "build", "-q", "-t", tag, "-f", str(dockerfile), str(ROOT)],
                LOCAL / "verification/docker-context-build.log",
            )
            container = run(
                ["docker", "create", "--label", "codex.public-medical.context=owned", tag, "/noop"],
                LOCAL / "verification/docker-context-create.log",
            )
            archive_path = LOCAL / "verification/docker-context.tar"
            run(
                ["docker", "export", "-o", str(archive_path), container],
                LOCAL / "verification/docker-context-export.log",
            )
            with tarfile.open(archive_path) as archive:
                names = archive.getnames()
            docker |= {
                "built": True,
                "file_count": len(names),
                "forbidden": [n for n in names if forbidden(n)],
                "public_corpus_in_context": any("public-prose.json" in n for n in names),
            }
    finally:
        if container:
            run(["docker", "rm", container], LOCAL / "verification/docker-context-remove.log")
        run(["docker", "image", "rm", tag], LOCAL / "verification/docker-context-image-remove.log")
        docker["owned_resources_removed"] = True
    # Compare configured credential values in memory; never log any values.
    credentials = []
    for env in [ROOT / ".env", ROOT.parent / "metaAgent/.env"]:
        if env.exists():
            credentials.extend(
                v.encode()
                for k, v in dotenv_values(env).items()
                if v and len(v) >= 16 and any(t in k.upper() for t in ["KEY", "TOKEN", "PASSWORD"])
            )
    paths = [
        ROOT / "README.md",
        ROOT / "pyproject.toml",
        ROOT / ".gitignore",
        ROOT / ".dockerignore",
    ]
    for directory in [
        "artifacts/public-medical-corpus-v1",
        "eval/public_medical_corpus_v1",
        "artifacts/medical-business-v1",
        "eval/medical_business_v1",
    ]:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file())
    paths.extend((ROOT / "src/rag_portfolio").glob("public_medical*.py"))
    paths.extend((ROOT / "tools").glob("*public_medical*.py"))
    paths.extend((ROOT / "docs").glob("public-medical-corpus-v1*.md"))
    paths.extend((ROOT / "src/rag_portfolio").glob("medical_release*.py"))
    paths.extend((ROOT / "tools").glob("*medical_business*.py"))
    paths.extend((ROOT / "tools").glob("*medical_release*.py"))
    paths.extend((ROOT / "docs").glob("medical-business*.md"))
    hits = [
        str(p.relative_to(ROOT))
        for p in paths
        if any(secret in p.read_bytes() for secret in credentials)
    ]
    report = {
        "packages": packages,
        "docker_context": docker,
        "credential_scan": {
            "files": len(paths),
            "actual_configured_secret_matches": hits,
            "values_logged": False,
        },
        "clinical_approval": False,
        "status": "PASS"
        if (
            not hits
            and len(packages) == 2
            and all(not p["forbidden"] and not p["missing_release_modules"] for p in packages)
            and not docker["forbidden"]
        )
        else "FAIL",
    }
    write_json(output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "packages": len(packages),
                "docker_forbidden": docker.get("forbidden"),
                "credential_match_count": len(hits),
            }
        )
    )
    if report["status"] != "PASS":
        raise ValueError("Publication boundary check failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", type=Path, default=LOCAL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    main(local=args.local.resolve(), output=args.output)
