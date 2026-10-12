"""Seal source-authored business cases before any retrieval; private anchors stay local."""

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from rag_portfolio.public_medical import canonical, immutable_json, sha, validate_corpus


def anchor(corpus, document, needle):
    for segment in corpus["segments"]:
        if segment["document_id"] != document:
            continue
        text = segment["text"]
        positions = [i for i, ch in enumerate(text) if not ch.isspace()]
        compact = "".join(text[i] for i in positions)
        target = "".join(needle.split())
        offset = compact.casefold().find(target.casefold())
        if offset < 0:
            continue
        start, end = positions[offset], positions[offset + len(target) - 1] + 1
        raw = text[start:end]
        doc = next(d for d in corpus["documents"] if d["document_id"] == document)
        return {
            "document_id": document,
            "segment_id": segment["segment_id"],
            "start": start,
            "end": end,
            "text": raw,
            "text_sha256": sha(raw.encode()),
            "source_sha256": doc["source_sha256"],
            "page": segment["page"],
            "locator": segment["locator"],
            "coordinate_system": segment["coordinate_system"],
        }
    raise ValueError(f"Author anchor absent: {document}: {needle}")


def main(args):
    corpus = json.loads(args.corpus.read_text(encoding="utf8"))
    validate_corpus(corpus)
    draft = json.loads(args.draft.read_text(encoding="utf8"))
    cases = draft["cases"]
    assert len(cases) == 40 and len({c["case_id"] for c in cases}) == 40
    assert Counter(c["split"] for c in cases) == {"dev": 24, "heldout": 16}
    assert len(Counter(c["category"] for c in cases)) == 8
    for category in {c["category"] for c in cases}:
        assert Counter(c["split"] for c in cases if c["category"] == category) == {
            "dev": 3,
            "heldout": 2,
        }
    if (args.output / "seal.json").exists():
        raise ValueError("Business seal already exists; never rewrite opened questions")
    args.private.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    pins = {}
    for split in ["dev", "heldout"]:
        public, private = [], []
        for case in (c for c in cases if c["split"] == split):
            safe = {k: v for k, v in case.items() if k != "turns"}
            safe["turns"] = []
            authored = {**safe, "turns": []}
            for turn in case["turns"]:
                if re.search(r"\b(?:NEW0[1-6]|D\d{3})\b", turn["question"]):
                    raise ValueError("Business user cannot be given internal document IDs")
                value = {k: v for k, v in turn.items() if k != "anchors"}
                safe["turns"].append(value)
                groups = []
                for document, needle in turn.get("anchors", []):
                    a = anchor(corpus, document, needle)
                    groups.append({"acceptable": [a], "preferred": [a]})
                authored["turns"].append({**value, "evidence_groups": groups})
            public.append(safe)
            private.append(authored)
        for folder, rows, key in [(args.output, public, "cases"), (args.private, private, "gold")]:
            path = folder / f"{split}.jsonl"
            if path.exists():
                raise ValueError("Versioned question file already exists")
            data = b"".join(canonical(row) + b"\n" for row in rows)
            path.write_bytes(data)
            pins[f"{split}_{key}_sha256"] = sha(data)
    approval = {
        "schema_version": "medical-processing-approval-v1",
        "authority": "human_user_current_task",
        "recorded_at": datetime.now(UTC).isoformat(),
        "user_confirmation": (
            "7篇旧资源可用；第三方图表、附件、同行评审往来已排除；同作品取最新版本；"
            "D042是2023版/2024发表。可以，推进该方案。"
        ),
        "external_model_processing": True,
        "public_redistribution": False,
        "clinical_approval": False,
        "corpus_sha256": corpus["corpus_sha256"],
        "documents": [
            {"document_id": d["document_id"], "source_sha256": d["source_sha256"]}
            for d in corpus["documents"]
        ],
        "license_handling": (
            "User processing confirmation does not rewrite publisher license fields."
        ),
    }
    approval["approval_sha256"] = sha(canonical(approval))
    immutable_json(args.private.parent / "processing_approval.json", approval)
    seal = {
        "schema_version": "medical-business-gold-seal-v1",
        "frozen_at": datetime.now(UTC).isoformat(),
        "corpus_sha256": corpus["corpus_sha256"],
        "case_count": 40,
        "split_counts": {"dev": 24, "heldout": 16},
        "category_counts": dict(Counter(c["category"] for c in cases)),
        **pins,
        "source_authoring": "Codex offline source reading; exact normalized source anchors",
        "independent_human_review": "pending",
        "clinical_approval": False,
        "protocol": {
            "strategy": "hybrid_rerank",
            "strategy_selection": "none; fixed existing default",
            "top_k": 5,
            "retrieval_budget": 4000,
            "tokenizer": "cl100k_base",
            "agent_context_budget": 12000,
            "agent_budget_unit": "UTF-8 bytes upper bound",
            "heldout_policy": (
                "one execution after dev; no tuning of model, prompt, retrieval or Gold"
            ),
            "multi_turn_retrieval": (
                "prior user turns + current user question; no answer or Gold query expansion"
            ),
            "semantic_grading": (
                "independent human pending; deterministic evidence/trace checks separate"
            ),
            "faults": (
                "explicit harness injected modes reported separately from real no-answer queries"
            ),
        },
    }
    immutable_json(args.output / "seal.json", seal)
    print(
        {
            "sealed": 40,
            "dev": 24,
            "heldout": 16,
            "private_gold": str(args.private),
            "corpus_sha256": corpus["corpus_sha256"],
        }
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--draft", type=Path, required=True)
    p.add_argument(
        "--corpus",
        type=Path,
        default=Path(".local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json"),
    )
    p.add_argument("--output", type=Path, default=Path("eval/medical_business_v1"))
    p.add_argument("--private", type=Path, default=Path(".local/medical_business_v1/gold"))
    main(p.parse_args())
