"""Record a bounded manual technical review of requested answer units, not all prose."""

import json
from pathlib import Path

from rag_portfolio.public_medical import sha, write_json

ROOT = Path(".local/public_medical_corpus_v1/agent/run-3")
# Labels were assigned by reading the actual Chinese answers and visible English spans.
# Substring assertions below only preserve traceability; they do not infer entailment.
UNITS = [
    ("S01", "38人", "new02", "s0013-c000", "The remaining 38"),
    ("S01", "病程小于6个月", "new02", "s0000-c000", "whose stroke onsets were <6 months"),
    ("S01", "实验17对照21", "new02", "s0013-c000", "The experimental group had 17 participants"),
    ("S01", "连续4周", "new02", "s0006-c001", "Both groups were trained for 4 consecutive weeks"),
    ("S01", "对照每周5天常规", "new02", "s0006-c000", "5 days a week"),
    (
        "S01",
        "实验每周3天机器人2天常规",
        "new02",
        "s0006-c000",
        "3 days a week and regular rehabilitation programs 2 days a week",
    ),
    (
        "S01",
        "每日总训练时长相同",
        "new02",
        "s0006-c001",
        "total training time for each day was the same",
    ),
    (
        "S01",
        "患者和评估者未盲",
        "new02",
        "s0006-c000",
        "Neither the patients nor the assessors were blinded",
    ),
    (
        "S02",
        "NEW02部分组间结局更优",
        "new02",
        "s0015-c000",
        "experimental group outperformed the control group",
    ),
    ("S03", "平均985步", "new05", "s0002-c000", "985(SD579) steps"),
    ("S03", "平均10有氧分钟", "new05", "s0002-c000", "a mean of 10(SD11) aerobic minutes"),
    ("S03", "R平方0.003关联可忽略", "new05", "s0002-c000", "negligible (R2 = 0.003)"),
    ("S03", "这些是群体观察均值而非个人处方", "new05", "s0002-c000", "Participants had a mean"),
    (
        "S04",
        "去重前1110项48099人",
        "new06",
        "s0021-c000",
        "1110 and the number of participants was 48,099",
    ),
    (
        "S04",
        "去重后341项14522人",
        "new06",
        "s0021-c000",
        "341 RCTs were included in SRs involving 14,522 participants",
    ),
    (
        "S04",
        "重叠综述计数不能相加当独立人群",
        "new06",
        "s0011-c000",
        "To avoid double counting outcome data",
    ),
]


def main():
    report = json.loads((ROOT / "cases.json").read_text(encoding="utf8"))
    cases = {c["case_id"]: c for c in report["cases"]}
    rows = []
    for case_id, claim, doc, section, phrase in UNITS:
        c = cases[case_id]
        snippets = [s for e in c["evidence"] for s in e["payload"].get("snippets", [])]
        source = next(
            s for s in snippets if s["doc_ref"].startswith(doc + "@") and s["section_id"] == section
        )
        assert phrase in source["text"]
        rows.append(
            {
                "case_id": case_id,
                "claim_unit": claim,
                "manual_technical_label": "supported",
                "source_doc_ref": source["doc_ref"],
                "source_text_sha256": source["text_sha256"],
                "verbatim_anchor": phrase,
                "citation_present_in_answer": f"[{source['doc_ref']}]" in c["answer"],
            }
        )
    write_json(
        Path("artifacts/public-medical-corpus-v1/agent_core_claims.json"),
        {
            "reviewer": "Codex source/answer reading; independent human pending",
            "clinical_approval": False,
            "denominator": "16 selected answered core units; one required NEW03 own-result "
            "unit unfilled; excludes extra Discussion facts and repeated summaries",
            "selection": "post-execution technical annotation; "
            "not a blinded general answer quality score",
            "source_trace_sha256": sha((ROOT / "cases.json").read_bytes()),
            "units": rows,
            "metrics": {
                "answered_core_units": 16,
                "source_supported": 16,
                "source_support_rate": 1.0,
                "contradicted": 0,
                "missing_citation_in_annotated_units": sum(
                    not r["citation_present_in_answer"] for r in rows
                ),
                "required_result_unit_unfilled": 1,
                "boundary_reason_errors": 1,
            },
            "limits": "Rates apply only to annotated core units. All-prose fact-error/"
            "missing-citation counts and clinical correctness remain unassessed.",
        },
    )
    print({"core_units": len(rows), "supported": 16, "required_unfilled": 1})


if __name__ == "__main__":
    main()
