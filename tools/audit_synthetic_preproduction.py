"""Inspect actual distributions, public files and predecessor preservation without logging keys."""

import argparse
import hashlib
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

from dotenv import dotenv_values
from verify_public_medical_package import forbidden

ROOT = Path(__file__).resolve().parents[1]


def main(args):
    local = args.local.resolve()
    if local.exists():
        raise ValueError("Keep audit attempts")
    local.mkdir(parents=True)
    roots = {"rag": ROOT, "meta": args.meta_repo.resolve()}
    keys = [
        v.encode()
        for root in roots.values()
        for k, v in dotenv_values(root / ".env").items()
        if v and len(v) >= 16 and any(s in k.upper() for s in ["KEY", "TOKEN", "PASSWORD"])
    ]
    report = dict(packages=[], preservation={}, credential_matches=[], clinical_approval=False)
    before = json.loads((ROOT / ".local/synthetic_preproduction_v1/predecessor.json").read_bytes())
    for name, root in roots.items():
        dist = local / name
        r = subprocess.run(["uv", "build", "--out-dir", str(dist)], cwd=root, capture_output=True)
        if r.returncode:
            raise RuntimeError("Package build failed")
        module = "preproduction_service.py" if name == "rag" else "preprod_api.py"
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
            report["packages"].append(
                dict(
                    repo=name,
                    name=path.name,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    new_module_packaged=any(n.endswith(module) for n in entries),
                    forbidden=[
                        n
                        for n in entries
                        if forbidden(n)
                        or n.endswith(".sqlite")
                        or "synthetic_preproduction_v1" in n
                        or "synthetic-preproduction-v1/" in n
                    ],
                    credential_matches=[
                        n for n, data in entries.items() if any(key in data for key in keys)
                    ],
                )
            )
        allowed = (
            {".dockerignore", ".gitattributes", "pyproject.toml", "README.md"}
            if name == "rag"
            else set()
        )
        protected = {n: p for n, p in before[name]["hashes"].items() if n not in allowed}
        report["preservation"][name] = dict(
            files=len(protected),
            changed=[
                n
                for n, pin in protected.items()
                if hashlib.sha256((root / n).read_bytes()).hexdigest() != pin
            ],
        )
        names = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root, text=True
        ).splitlines()
        report["credential_matches"] += [
            str(root / n)
            for n in names
            if (root / n).is_file() and any(key in (root / n).read_bytes() for key in keys)
        ]
    review = ROOT / ".local/synthetic_preproduction_v1/live-run-1/private-model-review.json"
    report["private_capture_contains_existing_keys"] = any(
        key in review.read_bytes() for key in keys
    )
    report["status"] = (
        "PASS"
        if len(report["packages"]) == 4
        and not report["credential_matches"]
        and not report["private_capture_contains_existing_keys"]
        and all(
            not p["forbidden"] and not p["credential_matches"] and p["new_module_packaged"]
            for p in report["packages"]
        )
        and all(not p["changed"] for p in report["preservation"].values())
        else "FAIL"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(json.dumps(report, indent=2).encode() + b"\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "status",
                    "preservation",
                    "credential_matches",
                    "private_capture_contains_existing_keys",
                ]
            }
        )
    )
    if report["status"] != "PASS":
        raise ValueError("Delivery audit failed")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--local", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    main(p.parse_args())
