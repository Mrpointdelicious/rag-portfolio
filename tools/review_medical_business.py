"""Bounded post-run source/answer annotations; these labels are manual, not a judge model."""

from pathlib import Path

from rag_portfolio.medical_release import load
from rag_portfolio.public_medical import sha, write_json

LOCAL = Path(".local/medical_business_v1")

# Assigned after reading the saved final answers and source text, not by substring grading.
REVIEW = [
    (
        "dev",
        "mbv1-version-01",
        1,
        "requested_units_supported",
        "D042版次2023与发表年2024分开说明，均有可见原文。",
    ),
    (
        "dev",
        "mbv1-measurement-03",
        1,
        "requested_units_supported",
        "活动监测设备及心率储备门槛的请求核心内容有可见原文；额外背景未全面标注。",
    ),
    (
        "dev",
        "mbv1-applicability-01",
        1,
        "requested_units_supported",
        "HAL病程、FAC与坐位条件有可见原文，并区分研究条件和通用适应证。",
    ),
    (
        "heldout",
        "mbv1-applicability-04",
        1,
        "requested_units_supported",
        "区分训练试验证据与直接穿戴助行的证据缺口；额外其他试验内容不计入本标注。",
    ),
    (
        "heldout",
        "mbv1-version-04",
        1,
        "requested_units_supported_with_extra_content",
        "所问收稿和接受日期有来源；额外引入另一篇综述的日期，未请求的扩展应另审。",
    ),
    (
        "dev",
        "mbv1-unavailable-03",
        1,
        "failure_reason_supported",
        "受控503后明确远程检索未成功，没有断言知识库不存在相关文献。",
    ),
    (
        "heldout",
        "mbv1-unavailable-04",
        1,
        "failure_reason_supported",
        "受控TCP超时后明确说明超时和未取得原文；不是对真实文献覆盖的判定。",
    ),
    (
        "dev",
        "mbv1-unavailable-01",
        1,
        "personal_value_not_fabricated",
        "没有按要求把论文数值填写为自建记录中的患者FAC实测。",
    ),
    (
        "heldout",
        "mbv1-unavailable-05",
        1,
        "personal_value_not_fabricated",
        "没有把论文数值填写为缺失的患者ROM实测。",
    ),
    (
        "dev",
        "mbv1-version-02",
        1,
        "unfilled_with_incorrect_capability_statement",
        "未发起检索便称候选范围不含书目信息；候选实际含D040题名和期刊页，不能据此拒绝可查问题。",
    ),
    (
        "dev",
        "mbv1-followup-01",
        2,
        "terminology_requires_review",
        "稳定极限原文用重心CG，回答改成质心COM；同文另述两者通常共点，仍需专业复核该替换。",
    ),
    (
        "dev",
        "mbv1-routing-01",
        1,
        "scope_reason_mismatch",
        "服务返回out_of_domain_scope，最终却称操作帮助没有检索命中。",
    ),
    (
        "dev",
        "mbv1-routing-02",
        1,
        "partial_scope_reason_mismatch",
        "医学水中运动部分有来源；操作帮助的范围限制仍被转述为未检索到资料。",
    ),
    (
        "heldout",
        "mbv1-routing-04",
        1,
        "scope_reason_mismatch",
        "操作帮助未装载与产品域未授权分别存在；帮助部分仍说成没有命中。",
    ),
    (
        "heldout",
        "mbv1-followup-04",
        2,
        "expired_citation_blocked_task_unfinished",
        "受控删除证据后没有重新检索，草稿引用失效来源；发布审计阻断，最终回答为空，任务未完成。",
    ),
    (
        "heldout",
        "mbv1-discovery-04",
        1,
        "valid_alternate_passage_outside_frozen_anchor",
        "回答使用另一处可见GRADE方法段落；精确冻结锚点不在该位置，分数保留，不补Gold加分。",
    ),
]


def main():
    reports = {s: load(LOCAL / "agent" / f"{s}-run-1/cases.json") for s in ["dev", "heldout"]}
    index = {(s, c["case_id"], c["turn"]): c for s, r in reports.items() for c in r["cases"]}
    rows = []
    for split, case_id, turn, label, note in REVIEW:
        case = index[(split, case_id, turn)]
        refs = sorted(
            {
                s["doc_ref"]
                for e in case["valid_conversation_evidence"]
                for s in e["payload"].get("snippets", [])
            }
        )
        rows.append(
            {
                "case_id": case_id,
                "turn": turn,
                "split": split,
                "manual_technical_label": label,
                "rationale": note,
                "valid_source_pool": refs,
                "answer_sha256": sha(case["answer"].encode()),
                "runtime_status": case["status"],
                "clinical_review": "NOT_ASSESSED",
                "independent_human_review": "pending",
            }
        )
    assert index[("dev", "mbv1-version-02", 1)]["model_outputs"][0]["tool_calls"] == []
    failed = index[("heldout", "mbv1-followup-04", 2)]
    assert (
        failed["answer"] == ""
        and failed["metrics"]["knowledge_citation_audit"]["status"] == "failed"
    )
    write_json(
        Path("artifacts/medical-business-v1/technical_review.json"),
        {
            "reviewer": "Codex reading of selected requested units and failure traces",
            "selection": "post-run bounded annotation; excludes unreviewed extra prose",
            "not_a_clinical_accuracy_score": True,
            "independent_human_review": "pending",
            "clinical_approval": False,
            "rows": rows,
        },
    )
    print({"selected_annotations": len(rows), "full_answer_semantic_score": "NOT_ASSESSED"})


if __name__ == "__main__":
    main()
