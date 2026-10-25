"""Fit on calibration examples only, report separate held-out validation outcomes."""

import argparse
import hashlib
import json
from pathlib import Path


def calibrate(rows):
    thresholds, report = {}, {}
    for domain, code in [("help", "hospital_kb"), ("health", "rehab_kb")]:
        fit = [row for row in rows if row["domain"] == domain and row["split"] == "calibration"]
        positives = [max(row["scores"], default=0) for row in fit if row["relevant"]]
        negatives = [max(row["scores"], default=0) for row in fit if not row["relevant"]]
        if not positives or not negatives or min(positives) <= max(negatives):
            raise ValueError(f"No clean score separation for {code}; review labels and policy")
        threshold = round((min(positives) + max(negatives)) / 2, 6)
        thresholds[code] = threshold
        held = [row for row in rows if row["domain"] == domain and row["split"] == "validation"]
        report[code] = {
            "threshold": threshold,
            "calibration_cases": len(fit),
            "validation_cases": len(held),
            "validation_correct": sum(
                (max(row["scores"], default=0) >= threshold) == row["relevant"] for row in held
            ),
        }
    return thresholds, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.baseline.read_text(encoding="utf-8").splitlines()]
    if len(rows) != 100 or any("error" in row or row.get("notes") for row in rows):
        raise ValueError("Calibration requires all 100 successfully reranked baseline examples")
    thresholds, report = calibrate(rows)
    root = Path(__file__).resolve().parents[1]
    dataset = [
        {k: r[k] for k in ("id", "domain", "query", "relevant", "split")}
        for r in sorted(rows, key=lambda r: r["id"])
    ]
    folder = root / "eval/relevance-v1"
    folder.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(dataset, ensure_ascii=False, indent=2).encode()
    (folder / "cases.json").write_bytes(raw)
    policy = {
        "version": "qwen37-relevance-v1",
        "rerank_model": "qwen3.7-text-rerank",
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "thresholds": thresholds,
    }
    (root / "src/rag_portfolio/relevance_policy.json").write_text(
        json.dumps(policy, indent=2), encoding="utf-8"
    )
    (folder / "calibration.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
