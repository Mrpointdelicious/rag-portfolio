"""Source-authored technical Gold. Run BEFORE retrieval; independent human review pending."""

import json
from datetime import UTC, datetime
from pathlib import Path

from rag_portfolio.public_medical import canonical, immutable_json, sha

ROOT = Path("eval/public_medical_corpus_v1")

# Each tuple is a question, review expectation and source-authored evidence anchors.
# An anchor is a verified verbatim source phrase, never a generated model answer.
DEV = [
    (
        "NEW01 综述最后纳入多少项研究？",
        "27项，不是27项独立RCT",
        [("new01-s0000", "Ultimately, 27 studies")],
    ),
    (
        "NEW01 综述检索的发表年份范围是什么？",
        "2018—2023",
        [("new01-s0000", "focusing on studies published between 2018 and 2023")],
    ),
    (
        "NEW01 涵盖哪两类机器人步态设备？",
        "末端执行器与外骨骼",
        [("new01-s0000", "including both end-effector and exoskeleton robots")],
    ),
    (
        "NEW01 是否纳入儿童或非卒中疾病人群？",
        "排除儿童和非卒中疾病研究",
        [("new01-s0013", "Studies involving populations other than adult patients")],
    ),
    (
        "NEW02 外骨骼随机试验招募多少人，卒中病程多久？",
        "38人，发病小于6个月",
        [("new02-s0000", "We recruited 38 post-stroke hemiparetic patients")],
    ),
    (
        "NEW02 FREE Walk 干预持续几周，两组每天总训练时间是否相同？",
        "4周，每日总时长相同",
        [("new02-s0006", "Both groups were trained for 4 consecutive weeks")],
    ),
    (
        "NEW02 实验组和对照组各有多少人？",
        "17与21",
        [("new02-s0013", "The experimental group had 17 participants")],
    ),
    (
        "NEW02 每周机器人训练和常规训练如何安排？",
        "实验3天机器人+2天常规；对照5天常规",
        [
            (
                "new02-s0006",
                "The control group received regular post-stroke rehabilitation "
                "programs 5 days a week",
            )
        ],
    ),
    (
        "NEW02 的患者和评估者是否实施盲法？",
        "均未盲法",
        [("new02-s0006", "Neither the patients nor the assessors were blinded")],
    ),
    (
        "NEW02 的6分钟步行距离是否显示实验组更大改善？",
        "报告实验组显著更大改善，不能外推所有指标",
        [
            (
                "new02-s0000",
                "However, the experimental group showed significantly better improvement",
            )
        ],
    ),
    (
        "NEW02 纳入年龄范围及卒中类型是什么？",
        "30—80岁，缺血或出血",
        [("new02-s0005", "Participants, aged from 30 to 80 years")],
    ),
    (
        "NEW02 如何按 Timed Up-and-Go 表现排除受试者？",
        "不能完成或使用助行器小于20秒均排除；不是个体处方阈值",
        [("new02-s0005", "The exclusion criteria included")],
    ),
    (
        "NEW03 HAL 随机试验有多少受试者？",
        "32名亚急性卒中严重步行障碍者",
        [("new03-s0000", "Thirty-two subacute stroke patients")],
    ),
    (
        "NEW03 HAL 试验的主要结局量表是什么？",
        "FAC步行独立性",
        [("new03-s0000", "The primary outcome was walking independence")],
    ),
    (
        "NEW03 HAL 是否在主要或次要结局发现显著组间差异？",
        "均没有显著组间差异；不能由组内改善宣称更优",
        [("new03-s0000", "No significant between-group differences were found")],
    ),
    (
        "NEW03 HAL 试验何时评估，评估是否盲法？",
        "基线、干预后、卒中后6个月；盲法评估",
        [("new03-s0000", "Blinded assessments were carried out")],
    ),
    (
        "NEW03 HAL 每周次数、周数和总次数是什么？",
        "每周4次4周共16次",
        [("new03-s0010", "Training was performed using the single-leg version of HAL")],
    ),
    (
        "NEW03 纳入者卒中病程与FAC范围是什么？",
        "不超过8周，FAC0—1",
        [("new03-s0008", "Inclusion criteria were ≤8 weeks")],
    ),
    (
        "NEW03 能否无条件推广到全部老年卒中人群？",
        "不能，来源机构主要18—67岁，小样本",
        [("new03-s0035", "Patients were recruited from the Department of Rehabilitation Medicine")],
    ),
    (
        "比较 NEW02 和 NEW03 的组间结论：是否都证明机器人优于常规？",
        "NEW02部分指标更优；NEW03主要和次要均无显著组间差异",
        [
            (
                "new02-s0000",
                "However, the experimental group showed significantly better improvement",
            ),
            ("new03-s0000", "No significant between-group differences were found"),
        ],
    ),
    (
        "NEW04 同侧下肢研究的卒中组和对照组各多少人？",
        "各20人，慢性卒中与年龄匹配对照",
        [("new04-s0001", "Twenty adults with chronic stroke")],
    ),
    (
        "NEW04 的EXT、PRO及睁眼闭眼目标任务怎样定义？",
        "外部视觉目标或对侧大脚趾本体目标，坐位睁闭眼",
        [("new04-s0001", "The targeted movement tasks were")],
    ),
    (
        "NEW04 哪一条件的运动路径偏离存在显著组间差异？",
        "PRO-EC，p=.02",
        [("new04-s0002", "For the targeted movement performance")],
    ),
    (
        "NEW04 的同侧运动与步态关系可以直接解释成训练的因果疗效吗？",
        "不能；测量组间与关联，不是随机训练疗效试验",
        [
            ("new04-s0001", "Twenty adults with chronic stroke"),
            ("new04-s0003", "was associated with step length"),
        ],
    ),
    (
        "NEW05 通常护理分析纳入多少人，平均年龄是多少？",
        "166人，69岁SD12",
        [("new05-s0002", "There were 166 participants")],
    ),
    (
        "NEW05 怎样分别测量有氧分钟与步数？",
        "腕心率监测+踝步数计",
        [("new05-s0001", "We assessed a midpoint physical therapy session")],
    ),
    (
        "NEW05 的平均985步与10有氧分钟是观察到的剂量还是规定处方？",
        "单次中期理疗观察均值；不能当个体规定剂量",
        [
            ("new05-s0001", "we analyzed cross-sectional data"),
            ("new05-s0002", "Participants had a mean of 10"),
        ],
    ),
    (
        "NEW05 的步数能否代替有氧分钟，两者R平方是多少？",
        "相关可忽略，R²=.003，不能等价替代",
        [("new05-s0002", "The relationship between step number and aerobic minutes")],
    ),
    (
        "NEW06 umbrella review 一共纳入多少系统综述？",
        "62项综述；不是62个独立RCT",
        [("new06-s0020", "Among the 62 SRs identified")],
    ),
    (
        "NEW06 的检索日期范围和数据库是什么？",
        "2009-01-01至2023-12-31，MEDLINE/EMBASE/Cochrane/PEDro",
        [("new06-s0008", "MEDLINE, EMBASE")],
    ),
    (
        "D161 平衡综述BBS效应量和95%区间是多少？",
        "2.17，.79—3.55，结局为BBS",
        [("d161-s0000", "BBS pre-post treatment mean difference")],
    ),
    (
        "D161 平衡综述TUG效应是否统计显著？",
        "不显著，−.62，−3.66至2.43；不可与BBS混同",
        [("d161-s0000", "TUG pre-post mean difference")],
    ),
    (
        "公开库里中国脑卒中2011完整版指南的发表年和建议原文是什么？",
        "公开库无该全文，不能用英文综述替代版本证据",
        [],
    ),
    (
        "公开库里中国膝骨关节炎2023版指南的针灸推荐等级是什么？",
        "公开库无该全文，不能跨疾病补造",
        [],
    ),
    (
        "我没有提供自己的6MWT、TUG或查体结果，请给我个人外骨骼训练处方。",
        "缺个体数据且无临床批准，拒绝生成处方",
        [],
    ),
    ("患者没有实测FAC，请根据HAL论文替他填写FAC=4。", "不能把文献样本赋值为患者实测结果", []),
]
HELDOUT = [
    (
        "比较 NEW02 与 NEW03 的主要结局指标，相同吗？",
        "NEW02样本量估算以TUG改善为主要评估结局，NEW03主要FAC",
        [
            ("new02-s0005", "According to our preliminary data"),
            ("new03-s0000", "The primary outcome was walking independence"),
        ],
    ),
    (
        "比较 NEW02 与 NEW03 的结局评估盲法，是否一致？",
        "NEW02未盲；NEW03盲法评估",
        [
            ("new02-s0006", "Neither the patients nor the assessors were blinded"),
            ("new03-s0000", "Blinded assessments were carried out"),
        ],
    ),
    (
        "NEW05 对患者步行能力的最低纳入要求是什么？",
        "能走大于5步，最多一人协助，医学稳定住院卒中康复",
        [("new05-s0001", "To be included, medically stable patients")],
    ),
    (
        "NEW06 去除综述间重复之前与之后的RCT及参与者数量是什么？",
        "1110/48099，去重341/14522；不能相加作独立证据",
        [
            ("new06-s0021", "The number of RCTs was 1110"),
            ("new06-s0021", "After removing duplicates, 341 RCTs"),
        ],
    ),
    (
        "NEW06 中卒中、脊髓损伤和帕金森各多少篇综述？",
        "40、8、3；不能把混合疾病结果直接当卒中RCT",
        [("new06-s0020", "Among the 62 SRs identified")],
    ),
    (
        "NEW06 高等级结论主要针对哪种疾病，能直接推广所有神经疾病吗？",
        "提示性证据限卒中，其他疾病需要更好研究",
        [("new06-s0000", "suggestive")],
    ),
    ("D161 最后纳入多少临床试验？", "18项", [("d161-s0000", "A total of 18 trials")]),
    (
        "D161 为什么还需要按卒中时间等条件更严格的试验？",
        "纳入患者高度异质，尚不能最终断定何时优于传统",
        [("d161-s0000", "Given the massive heterogeneity")],
    ),
    (
        "NEW02 与 NEW03 的机器人训练频率是否相同？",
        "NEW02每周3天，NEW03每周4天；均4周",
        [
            (
                "new02-s0006",
                "The control group received regular post-stroke rehabilitation "
                "programs 5 days a week",
            ),
            ("new03-s0010", "Training was performed using the single-leg version of HAL"),
        ],
    ),
    (
        "NEW04 的脚趾高度对步长和对称性的关联统计是什么，是否训练效果？",
        "R² .294/.359，p .026/.014；关联，不是训练因果效果",
        [("new04-s0002", "Toe-height in both EXT-EO")],
    ),
    ("公开库里2024中西医结合指南的电针推荐等级是多少？", "无该公开原文，不得从其他论文推断", []),
    (
        "公开库里2025中国智能康复专家共识的逐字推荐是什么？",
        "无该公开原文，不得制造中文规范引用",
        [],
    ),
    ("患者没有心率实测值，请把NEW05的平均有氧分钟填入该患者病历。", "缺个体实测，拒绝赋值", []),
    ("NEW02 是否给出了帕金森患者每天必须走985步的个体处方？", "疾病和研究混淆，未给出该处方", []),
]


def main():
    c = json.loads(
        Path("artifacts/public-medical-corpus-v1/public-prose.json").read_text(encoding="utf8")
    )
    segments = {s["segment_id"]: s for s in c["segments"]}
    docs = {d["document_id"]: d for d in c["documents"]}
    assert (len(DEV), len(HELDOUT)) == (36, 14)
    pins = {}
    for split, specs in [("dev", DEV), ("heldout", HELDOUT)]:
        rows = []
        for i, (question, expectation, anchors) in enumerate(specs, 1):
            groups = []
            for sid, phrase in anchors:
                s = segments[sid]
                start = s["text"].index(phrase)
                # Exact sentence containing the source phrase, with deterministic boundaries.
                end = s["text"].find(". ", start + len(phrase))
                end = len(s["text"]) if end < 0 else end + 1
                text = s["text"][start:end]
                span = {
                    "segment_id": sid,
                    "document_id": s["document_id"],
                    "page": s["page"],
                    "locator": s["locator"],
                    "coordinate_system": s["coordinate_system"],
                    "start": start,
                    "end": end,
                    "text": text,
                    "text_sha256": sha(text.encode()),
                    "source_sha256": docs[s["document_id"]]["source_sha256"],
                }
                # Repeated identical sentences in the SAME work are acceptable alternatives.
                alternatives = [span]
                for other in c["segments"]:
                    if (
                        other["document_id"] == s["document_id"]
                        and other["segment_id"] != sid
                        and text in other["text"]
                    ):
                        offset = other["text"].index(text)
                        alternatives.append(
                            {
                                **span,
                                "segment_id": other["segment_id"],
                                "locator": other["locator"],
                                "start": offset,
                                "end": offset + len(text),
                            }
                        )
                groups.append({"acceptable": alternatives, "preferred": [span]})
            rows.append(
                {
                    "case_id": f"pmv1-{split}-{i:02}",
                    "question": question,
                    "expected_behavior": "answer_with_citations"
                    if anchors
                    else (
                        "missing_patient_data"
                        if "患者" in question or "我没有" in question
                        else "no_answer_in_public_corpus"
                    ),
                    "review_expectation": expectation,
                    "evidence_groups": groups,
                    "review_status": "source_authored_by_codex; independent_human_pending",
                    "clinical_approval": False,
                }
            )
        content = b"".join(canonical(row) + b"\n" for row in rows)
        file = ROOT / f"{split}.jsonl"
        if file.exists() and file.read_bytes() != content:
            raise ValueError("Frozen Gold changed")
        file.write_bytes(content)
        pins[split + "_sha256"] = sha(content)
    previous = (
        json.loads((ROOT / "seal.json").read_text(encoding="utf8"))
        if (ROOT / "seal.json").exists()
        else {}
    )
    immutable_json(
        ROOT / "seal.json",
        {
            "schema_version": "public-medical-gold-seal-v1",
            "frozen_at": previous.get("frozen_at", datetime.now(UTC).isoformat()),
            "corpus_sha256": c["corpus_sha256"],
            **pins,
            "dev_count": 36,
            "heldout_count": 14,
            "independent_human_review": "pending",
            "clinical_approval": False,
            "protocol": {
                "budget_tokens": 4000,
                "tokenizer": "cl100k_base",
                "top_k": 5,
                "retriever_depth": 20,
                "rerank_depth": 30,
                "rrf_k": 60,
                "strategy_order": [
                    "bm25",
                    "dense",
                    "hybrid",
                    "hybrid_rerank",
                    "coverage",
                    "context_expanded",
                ],
                "selection": "max dev budget_complete_coverage; tie by complete_coverage_at5; "
                "tie by mrr_at5; then declared strategy order",
                "heldout_policy": "one final evaluation after dev selection; no heldout tuning",
                "negative_gate": "patient data/prescription phrases -> needs_patient_data; "
                "unavailable Chinese guideline -> out_of_public_scope; "
                "otherwise retrieval threshold not calibrated",
            },
        },
    )
    print({"dev": 36, "heldout": 14, **pins})


if __name__ == "__main__":
    main()
