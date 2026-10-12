"""Freeze synthetic shared authority conditions and preserve predecessor bytes before coding."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/shared_authority_v3"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def main():
    if LOCAL.exists():
        raise ValueError("Preserve prior attempts")
    before = {}
    for name, root in {
        "rag": ROOT,
        "meta": Path("D:/Project/metaAgent"),
        "backend": Path("D:/Project/AI_WebApi"),
    }.items():
        files = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root
            )
            .decode()
            .splitlines()
        )
        before[name] = dict(
            root=str(root),
            head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
            status=subprocess.check_output(["git", "status", "--porcelain"], cwd=root).decode(),
            hashes={
                n: hashlib.sha256((root / n).read_bytes()).hexdigest()
                for n in files
                if (root / n).is_file()
            },
        )
    save(LOCAL / "predecessor.json", before)
    conditions = [
        "patient session derives actor and role from server login fixture",
        "clinician with relationship obtains session",
        "operator cannot obtain patient session",
        "unlinked actor denied",
        "different tenant denied",
        "client role injection rejected",
        "wrong issuer rejected by shared authority",
        "wrong audience rejected by .NET",
        "tampered signature rejected by .NET",
        "expired subject rejected by .NET",
        ".NET body user conflict rejected",
        ".NET body session conflict rejected",
        "actual .NET profile controller returns bound synthetic DTO",
        "native combined run completes using .NET and PostgreSQL",
        "native record run completes with zero model calls",
        "OS agent process restart preserves authorized snapshot",
        "second native worker on same DB refused",
        "revocation immediately blocks .NET process",
        "revocation blocks native snapshot",
        "revocation blocks knowledge process",
        "regrant does not revive old token",
        "same session cannot change grant generation",
        "new generation and new session complete",
        "authority offline .NET fails closed",
        "authority offline native snapshot fails closed",
        "old generation and different actor cannot read new run",
        "expired wrapper cleanup removes all scoped store entries",
        "expired wrapper cleanup physically removes checkpoint rows and blobs",
        "cross actor snapshot denied",
        "revoked citation replay denied",
        "actual native model messages contain no patient canaries or values",
        "OS authority restart preserves revocation",
        "linked backend source files remain byte identical",
        "missing .NET subject rejected",
        "revoked event replay denied",
        "OS .NET process restart retains current shared authorization",
    ]
    path = ROOT / "eval/shared_authority_v3/cases.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(
            (json.dumps(dict(id=f"A{i:02d}", condition=c), ensure_ascii=False) + "\n").encode()
            for i, c in enumerate(conditions, 1)
        )
    )
    save(
        path.with_name("seal.json"),
        dict(
            schema="shared-authority-v3",
            frozen_at=datetime.now(UTC).isoformat(),
            cases_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            case_count=len(conditions),
            engineering_only=True,
            synthetic_only=True,
            clinical_approval=False,
            acceptance="All conditions actually exercised and pass; all failed attempts retained",
        ),
    )
    print(dict(frozen_cases=len(conditions), predecessor_repos=list(before)))


if __name__ == "__main__":
    main()
