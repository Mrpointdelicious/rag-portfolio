"""Seal source-authored private draft; no Gold or source needles in public code."""

import argparse
import json
from collections import Counter
from datetime import UTC, datetime

from rag_portfolio.public_medical import canonical, sha


def freeze(draft, output, private, corpus_sha256):
    rows = json.loads(draft.read_text(encoding="utf8"))
    if (output / "seal.json").exists():
        raise ValueError("Never overwrite a sealed suite")
    assert len(rows) == 32 and len({r["case_id"] for r in rows}) == 32
    assert Counter(r["split"] for r in rows) == {"dev": 16, "heldout": 16}
    assert set(Counter(r["category"] for r in rows).values()) == {4}
    output.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    pins = {}
    for split in ["dev", "heldout"]:
        selected = [r for r in rows if r["split"] == split]
        for folder, key, values in [
            (output, "cases", [{k: v for k, v in r.items() if k != "anchors"} for r in selected]),
            (private, "gold", selected),
        ]:
            path = folder / f"{split}.jsonl"
            if path.exists():
                raise ValueError("Existing question or Gold file")
            raw = b"".join(canonical(v) + b"\n" for v in values)
            path.write_bytes(raw)
            pins[f"{split}_{key}_sha256"] = sha(raw)
    (output / "seal.json").write_bytes(
        canonical(
            dict(
                schema_version="medical-business-v2",
                frozen_at=datetime.now(UTC).isoformat(),
                corpus_sha256=corpus_sha256,
                case_count=32,
                turn_count=sum(len(r["turns"]) for r in rows),
                **pins,
            )
        )
    )


if __name__ == "__main__":
    from pathlib import Path

    p = argparse.ArgumentParser()
    p.add_argument("--draft", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--private", type=Path, required=True)
    p.add_argument("--corpus-sha256", required=True)
    a = p.parse_args()
    freeze(a.draft, a.output, a.private, a.corpus_sha256)
