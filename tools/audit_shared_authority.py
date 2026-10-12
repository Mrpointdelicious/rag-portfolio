"""Inspect real packages/build contexts, existing-secret exclusion and predecessor preservation."""

import argparse
import hashlib
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from verify_public_medical_package import forbidden

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def blocked(name):
    return (
        forbidden(name)
        or any(
            x in name
            for x in [
                "synthetic_preproduction_v1/",
                "synthetic-preproduction-v1/",
                "synthetic_native_v2/",
                "synthetic-native-v2/",
                "shared_authority_v3/",
                "shared-authority-v3/",
                "preproduction/patient-tools/",
            ]
        )
        or name.endswith(".sqlite")
    )


def main(args):
    out = args.output.resolve()
    if out.exists():
        raise ValueError("Preserve audit attempts")
    out.mkdir(parents=True)
    before = json.loads((ROOT / ".local/shared_authority_v3/predecessor.json").read_bytes())
    roots = {k: Path(v["root"]) for k, v in before.items()}
    secrets = [
        v.encode()
        for name, root in roots.items()
        if name != "backend"
        for k, v in dotenv_values(root / ".env").items()
        if v and len(v) >= 16 and any(x in k.upper() for x in ["KEY", "TOKEN", "PASSWORD"])
    ]
    report = dict(
        packages=[],
        contexts=[],
        preservation={},
        credential_matches=[],
        clinical_approval=False,
        configured_values_logged=False,
    )
    allowed = {
        "rag": {
            ".dockerignore",
            ".gitattributes",
            "pyproject.toml",
            "README.md",
            "tools/prepare_shared_authority.py",
        },
        "meta": {
            ".dockerignore",
            "pyproject.toml",
            "README.md",
            "src/meta_agent/preprod_native.py",
        },
        "backend": set(),
    }
    for name, root in roots.items():
        protected = {n: pin for n, pin in before[name]["hashes"].items() if n not in allowed[name]}
        report["preservation"][name] = dict(
            files=len(protected),
            changed=[
                n
                for n, pin in protected.items()
                if not (root / n).is_file() or sha(root / n) != pin
            ],
        )
        if name == "backend":
            continue
        files = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root
            )
            .decode()
            .splitlines()
        )
        report["credential_matches"] += [
            dict(repo=name, file=n)
            for n in files
            if (root / n).is_file() and any(key in (root / n).read_bytes() for key in secrets)
        ]
        dist = out / name
        build = subprocess.run(
            ["uv", "build", "--out-dir", str(dist)], cwd=root, capture_output=True
        )
        (out / f"{name}-build.log").write_bytes(build.stdout + build.stderr)
        if build.returncode:
            raise RuntimeError("Package build failed")
        for path in dist.iterdir():
            if path.suffix == ".whl":
                with zipfile.ZipFile(path) as archive:
                    entries = {
                        n: archive.read(n) for n in archive.namelist() if not n.endswith("/")
                    }
            elif path.name.endswith(".tar.gz"):
                with tarfile.open(path) as archive:
                    entries = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
            else:
                continue
            module = "preprod_shared.py" if name == "meta" else "preproduction_service.py"
            report["packages"].append(
                dict(
                    repo=name,
                    file=path.name,
                    sha256=sha(path),
                    forbidden=[n for n in entries if blocked(n)],
                    credential_matches=[
                        n for n, data in entries.items() if any(k in data for k in secrets)
                    ],
                    expected_module=any(n.endswith(module) for n in entries),
                )
            )
        dockerfile = out / f"{name}.Dockerfile"
        dockerfile.write_bytes(b'FROM scratch\nCOPY . /workspace/\nCMD ["/__inspection_only__"]\n')
        tag = "codex-shared-context-" + uuid4().hex[:12]
        cid = None
        context = dict(repo=name, forbidden=[], credential_matches=[], built=False, removed=False)
        try:
            build = subprocess.run(
                ["docker", "build", "-q", "-t", tag, "-f", str(dockerfile), str(root)],
                capture_output=True,
                timeout=180,
            )
            (out / f"{name}-context-build.log").write_bytes(build.stdout + build.stderr)
            if build.returncode:
                raise RuntimeError("Owned context build failed")
            context["built"] = True
            created = subprocess.run(
                ["docker", "create", tag], capture_output=True, text=True, timeout=30
            )
            if created.returncode:
                raise RuntimeError("Owned context inspection failed")
            cid = created.stdout.strip()
            archive_path = out / f"{name}-context.tar"
            exported = subprocess.run(
                ["docker", "export", "-o", str(archive_path), cid], capture_output=True, timeout=60
            )
            if exported.returncode:
                raise RuntimeError("Owned context export failed")
            with tarfile.open(archive_path) as archive:
                entries = [m for m in archive if m.isfile() and m.name.startswith("workspace/")]
                context["files"] = len(entries)
                for entry in entries:
                    if blocked(entry.name):
                        context["forbidden"].append(entry.name)
                    if any(k in archive.extractfile(entry).read() for k in secrets):
                        context["credential_matches"].append(entry.name)
        finally:
            removed = True
            if cid:
                removed &= (
                    subprocess.run(["docker", "rm", "-f", cid], capture_output=True).returncode == 0
                )
            removed &= (
                subprocess.run(["docker", "image", "rm", tag], capture_output=True).returncode == 0
            )
            context["removed"] = removed
            report["contexts"].append(context)
    prior = json.loads((ROOT / "artifacts/synthetic-native-v2/summary.json").read_bytes())
    report["v2_review_queue_preserved"] = (
        sha(ROOT / ".local/synthetic_native_v2/review/full-answer-records.json")
        == prior["review"]["private_sha256"]
    )
    local = ROOT / ".local/shared_authority_v3"
    private = [
        p
        for p in local.glob("contracts-run-*/*")
        if p.name in {"private-http-review.json", "model-inputs.jsonl", "model-outputs.jsonl"}
    ]
    report["private_evidence_existing_key_matches"] = [
        p.parent.name + "/" + p.name for p in private if any(k in p.read_bytes() for k in secrets)
    ]
    report["status"] = (
        "PASS"
        if (
            len(report["packages"]) == 4
            and len(report["contexts"]) == 2
            and not report["credential_matches"]
            and not report["private_evidence_existing_key_matches"]
            and report["v2_review_queue_preserved"]
            and all(not v["changed"] for v in report["preservation"].values())
            and all(
                not p["forbidden"] and not p["credential_matches"] and p["expected_module"]
                for p in report["packages"]
            )
            and all(
                c["built"] and c["removed"] and not c["forbidden"] and not c["credential_matches"]
                for c in report["contexts"]
            )
        )
        else "FAIL"
    )
    save(out / "result.json", report)
    print(
        dict(
            status=report["status"],
            packages=len(report["packages"]),
            contexts=len(report["contexts"]),
            source_changes={k: len(v["changed"]) for k, v in report["preservation"].items()},
        )
    )
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    main(p.parse_args())
