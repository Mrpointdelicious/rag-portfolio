"""Reuse actual package/context inspection with the v4 predecessor and explicit edit scope."""

import argparse
import json
from pathlib import Path

from audit_shared_authority import ROOT, main

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.local = ROOT / ".local/project_identity_v4"
    args.predecessor = args.local / "predecessor.json"
    args.allowed = {
        "rag": {
            ".dockerignore",
            ".gitattributes",
            "pyproject.toml",
            "README.md",
            "tools/audit_shared_authority.py",
            "tools/prepare_project_identity.py",
        },
        "meta": {"pyproject.toml", "uv.lock", "tools/shared_authority_worker.py", "README.md"},
        "backend": set(),
    }
    args.expected_modules = {"meta": "project_identity.py"}
    addendum = json.loads((ROOT / "eval/project_identity_v4/addendum-seal.json").read_bytes())
    args.extra_sources = addendum["sources"]
    prior = json.loads((ROOT / "artifacts/shared-authority-v3/summary.json").read_bytes())
    args.private_preservation = {
        "v3_review": {
            "path": str(ROOT / ".local/shared_authority_v3/review/full-answer-records.json"),
            "sha256": prior["new_review"]["private_sha256"],
        },
        "approved_combined_corpus": {
            "canonical_field": "corpus_sha256",
            "path": str(
                ROOT / ".local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json"
            ),
            "sha256": "0253a1c5453eb8ea9b4cc8d02cd49989c7b1f3f5dc956ff3774a1a5a3d33dace",
        },
        "business_dense_index": {
            "canonical_field": "index_sha256",
            "path": str(ROOT / ".local/medical_business_v1/indexes/combined-business-dense-1.json"),
            "sha256": "10e58204c4f25f1464bab5588fe38a5646594274fa452d70f6c4ccf0b2ee0b76",
        },
    }
    main(args)
