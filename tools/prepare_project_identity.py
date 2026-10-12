"""Freeze project identity policy acceptance before implementation; no patient rows copied."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def main():
    local = ROOT / ".local/project_identity_v4"
    if local.exists():
        raise ValueError("Preserve predecessor and failed attempts")
    before = {}
    for name, root in {
        "rag": ROOT,
        "meta": Path("D:/Project/metaAgent"),
        "backend": Path("D:/Project/AI_WebApi"),
    }.items():
        paths = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root
            )
            .decode()
            .splitlines()
        )
        before[name] = dict(
            root=str(root),
            head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
            hashes={
                n: hashlib.sha256((root / n).read_bytes()).hexdigest()
                for n in paths
                if (root / n).is_file()
            },
        )
    sources = [
        "D:/Project/Docker/mysql/docs/_project_schema_audit.md",
        "D:/Project/metaagent-ai-webapi-newdb/docs/database/aiwebapi-project-update-plan-20260915.md",
        "C:/Users/weidaohao/Desktop/dump-project-202609201008.sql",
    ]
    upstream = Path("D:/Project/JS.CloudPlatform.WebApi")
    sources += [
        str(upstream / p)
        for p in [
            "JS.WebCore.Api/Controllers/LoginController.cs",
            "JS.WebCore.Extensions/Authorizations/Policys/JwtToken.cs",
            "JS.WebCore.Extensions/ServiceExtensions/Authentication_JWTSetup.cs",
            "JS.WebCore.Model/Models/sysUserInfo.cs",
            "JS.WebCore.Model/Models/UserTenant.cs",
            "JS.WebCore.Model/Models/UserRole.cs",
            "JS.WebCore.Model/Models/SysRole.cs",
            "JS.WebCore.Model/Models/SysTenant.cs",
            "JS.WebCore.Model/Models/SysMedical.cs",
            "JS.WebCore.Services/SysMedicalServices.cs",
        ]
    ]
    before["sources"] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}
    save(local / "predecessor.json", before)
    conditions = [
        "valid project JWT patient obtains own project session",
        "doctor with active same-tenant consultation obtains session",
        "missing JWT denied",
        "wrong signature denied",
        "algorithm none denied",
        "wrong issuer denied",
        "wrong audience denied",
        "expired JWT denied",
        "future nbf denied",
        "ambiguous UserId list denied",
        "jti UserId mismatch denied",
        "school zero user denied",
        "token role claim cannot grant doctor role",
        "client actor role injection rejected",
        "unknown tenant alias denied",
        "other tenant membership cannot grant access",
        "unrelated doctor denied",
        "patient cannot read another patient",
        "operator role denied",
        "deleted user denied",
        "unknown account status denied",
        "disabled tenant denied",
        "deleted tenant denied",
        "missing actor membership denied",
        "disabled patient membership denied",
        "role from another tenant denied",
        "disabled role binding denied",
        "deleted role definition denied",
        "unknown role enable denied",
        "closed consultation alone denied",
        "disabled consultation denied",
        "wrong-tenant consultation denied",
        "stale open consultation denied",
        "future consultation date denied",
        "sentinel consultation date denied",
        "plan alone does not grant ongoing care access",
        "duplicate active memberships fail closed",
        "duplicate role bindings fail closed",
        "current project binding does not translate old IDs",
        "legacy robot mapping and name fallback unavailable",
        "read-only SQL user cannot write patient table",
        "fresh introspection observes closed consultation",
        "reopened consultation never revives observed revoked token",
        "new session after regrant succeeds",
        "old session cannot change generation",
        "database outage returns sanitized 503",
        "authority restart retains revocation",
        "native record uses actual linked dotnet controller with zero model calls",
        "revocation blocks native snapshot events and dotnet access",
        "old and ambiguous request routes unavailable",
        "backend and database documentation remain unchanged",
    ]
    path = ROOT / "eval/project_identity_v4/cases.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(
            (json.dumps(dict(id=f"P{i:02d}", condition=c)) + "\n").encode()
            for i, c in enumerate(conditions, 1)
        )
    )
    save(
        path.with_name("seal.json"),
        dict(
            schema="project-identity-v4",
            frozen_at=datetime.now(UTC).isoformat(),
            cases_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            case_count=len(conditions),
            synthetic_only=True,
            clinical_approval=False,
            policy="project identity only; recent open consultation <= 7 days; no plan-only grant",
        ),
    )
    print(dict(frozen_cases=len(conditions), source_files=len(sources)))


if __name__ == "__main__":
    main()
