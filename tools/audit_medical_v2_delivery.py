"""Inspect built Meta packages/runtime images and actual secret values without printing them."""

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
    args.meta_repo = args.meta_repo.resolve()
    args.output = args.output.resolve()
    local = (args.local or ROOT / ".local/medical_business_v2/delivery-audit").resolve()
    if local.exists():
        raise ValueError("Keep audit attempts")
    local.mkdir(parents=True)
    report = {"meta_packages": [], "images": [], "clinical_approval": False}
    secrets = []
    for root in [ROOT, args.meta_repo]:
        secrets.extend(
            v.encode()
            for k, v in dotenv_values(root / ".env").items()
            if v and len(v) >= 16 and any(t in k.upper() for t in ["KEY", "TOKEN", "PASSWORD"])
        )
    config = json.loads((ROOT / ".local/medical_business_v2/pilot/config.json").read_text())
    secrets.extend(config[k].encode() for k in ["rag_token", "agent_token"])

    def run(cmd, cwd=ROOT):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=180)
        if r.returncode:
            raise RuntimeError("Audit subprocess failed")
        return r.stdout.decode("utf8", errors="replace").strip()

    dist = local / "meta-dist"
    dist.mkdir()
    run(["uv", "build", "--out-dir", str(dist)], args.meta_repo)
    for path in dist.iterdir():
        if not path.name.endswith((".whl", ".tar.gz")):
            continue
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                hits = [n for n in names if any(s in archive.read(n) for s in secrets)]
        else:
            with tarfile.open(path) as archive:
                names = archive.getnames()
                hits = [
                    m.name
                    for m in archive
                    if m.isfile() and any(s in archive.extractfile(m).read() for s in secrets)
                ]
        report["meta_packages"].append(
            dict(
                name=path.name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                forbidden=[n for n in names if forbidden(n)],
                credential_matches=hits,
                pilot_ui_packaged=any(n.endswith("meta_agent/pilot.html") for n in names),
            )
        )
    for tag in ["medical-rag:internal-v2", "medical-metaagent:internal-v2"]:
        cid = None
        try:
            cid = run(
                ["docker", "create", "--label", "codex.medical-v2.audit=owned", tag, "/bin/true"]
            )
            path = local / (tag.split(":")[0] + ".tar")
            run(["docker", "export", "-o", str(path), cid])
            with tarfile.open(path) as archive:
                names = archive.getnames()
                bad = [
                    n
                    for n in names
                    if n.endswith("/.env")
                    or "/.local/" in n
                    or "/run/pilot/config.json" in n
                    or n.endswith(("combined-corpus.json", "combined-index.json"))
                ]
                hits = []
                for m in archive:
                    if m.isfile():
                        data = archive.extractfile(m).read()
                        if any(s in data for s in secrets):
                            hits.append(m.name)
            report["images"].append(
                dict(
                    tag=tag,
                    image_id=run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]),
                    forbidden=bad,
                    credential_matches=hits,
                )
            )
        finally:
            if cid:
                run(["docker", "rm", cid])
    scanned = 0
    matches = []
    for root in [ROOT, args.meta_repo]:
        files = run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"], root
        ).splitlines()
        for name in files:
            path = root / name
            if path.is_file():
                scanned += 1
                if any(s in path.read_bytes() for s in secrets):
                    matches.append(str(path))
    report["credential_scan"] = dict(files=scanned, matches=matches, values_logged=False)
    legacy = json.loads(
        (ROOT / ".local/public_medical_corpus_v1/legacy-baseline.json").read_text()
    )["files"]
    report["legacy_preservation"] = dict(
        files=len(legacy),
        changed=[
            name
            for name, pin in legacy.items()
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != pin
        ],
    )
    predecessor = json.loads(
        (ROOT / ".local/medical_business_v2/predecessor_snapshot.json").read_text()
    )["repos"]["rag"]["tracked_hashes"]
    protected = {
        n: s
        for n, s in predecessor.items()
        if n.startswith(
            (
                "eval/public_medical_corpus_v1/",
                "artifacts/public-medical-corpus-v1/",
                "eval/medical_business_v1/",
                "artifacts/medical-business-v1/",
                "docs/medical-business-v1",
                "docs/public-medical-corpus-v1",
            )
        )
    }
    report["v1_preservation"] = dict(
        files=len(protected),
        changed=[
            name
            for name, pin in protected.items()
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != pin
        ],
    )
    report["status"] = (
        "PASS"
        if not matches
        and len(report["meta_packages"]) == 2
        and len(report["images"]) == 2
        and not report["legacy_preservation"]["changed"]
        and not report["v1_preservation"]["changed"]
        and all(
            not p["forbidden"] and not p["credential_matches"] and p["pilot_ui_packaged"]
            for p in report["meta_packages"]
        )
        and all(not p["forbidden"] and not p["credential_matches"] for p in report["images"])
        else "FAIL"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ["status", "credential_scan", "legacy_preservation", "v1_preservation"]
            }
        )
    )
    if report["status"] != "PASS":
        raise ValueError("Delivery audit failed; inspect the retained report")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--local", type=Path)
    main(p.parse_args())
