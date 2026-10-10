"""Reviewable per-case reports and ablation interpretation from measured results."""

from rag_portfolio.evaluation.v2_metrics import QUALITY_KEYS


def cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers, rows) -> str:
    lines = [
        "| " + " | ".join(map(cell, headers)) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(map(cell, row)) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def metrics_table(items) -> str:
    return table(
        ["Group", "Count", *QUALITY_KEYS],
        [
            [name, metrics["count"], *(metrics[key] for key in QUALITY_KEYS)]
            for name, metrics in items
        ],
    )


def render_variant(report: dict) -> str:
    lines = [f"# {report['variant']} — {report['name']}", "", f"Run: {report['run_timestamp']}", ""]
    if report["status"] == "skipped":
        return "\n".join(
            [
                *lines,
                report["reason"],
                "",
                "29 cases 均跳过，没有质量指标。RerankerPort 已定义，尚无实际 provider。",
                "",
            ]
        )
    lines += [
        f"Score: `{report['score_type']}`; Dense source: `{report['dense_source_sha256']}`.",
        "",
        f"Corpus: `{report['corpus_fingerprint']}`; Gold: `{report['suite_fingerprint']}`.",
        "",
        (
            "5 documents / 43 chunks / 29 qu"
            "eries / 25 quality cases / Top-"
            "K=5. 冻结真实 Dense Top-5 回放。"
        ),
        "",
        metrics_table([("overall", report["overall"]), *report["categories"].items()]),
        "## No-answer / Answerability",
        "",
        report["no_answer"]["policy"],
        "",
        "Precision/recall 仅衡量 query 是否有答案，不代表所检索证据或生成回答正确。",
        "",
        table(
            ["Class", "Count", "Min", "Max", "Mean", "Median"],
            [
                [
                    name,
                    *(
                        report["no_answer"][f"{name}_distribution"][key]
                        for key in ("count", "min", "max", "mean", "median")
                    ),
                ]
                for name in ("positive", "negative")
            ],
        ),
        table(
            ["Positive case", "Top1 score"],
            [[row["id"], row["score"]] for row in report["no_answer"]["positives"]],
        ),
        table(
            ["No-answer case", "Top1", "Top2", "Margin"],
            [
                [row["id"], row["score"], row["top2_score"], row["margin"]]
                for row in report["no_answer"]["negatives"]
            ],
        ),
        "候选阈值覆盖全部观察分数的接受/拒绝分区，含 ties、全接收、全拒绝；只在本数据集诊断。",
        "",
        table(
            [
                "Threshold",
                "TP",
                "FP",
                "TN",
                "FN",
                "Precision",
                "Recall",
                "False accept",
                "False reject",
            ],
            [
                [
                    row[key]
                    for key in (
                        "threshold",
                        "TP",
                        "FP",
                        "TN",
                        "FN",
                        "precision",
                        "recall",
                        "false_accept_rate",
                        "false_reject_rate",
                    )
                ]
                for row in report["no_answer"]["threshold_candidates"]
            ],
        ),
        "完整精度阈值见 JSON；显示时舍入可能令临界值看起来相同。",
        "",
        "## Source conflict",
        "",
        table(
            ["Case", "ConflictRecall@2", "@3", "@5"],
            [
                [row["id"], *(row["metrics"][f"ConflictRecall@{k}"] for k in (2, 3, 5))]
                for row in report["source_conflict"]
            ],
        ),
        "C01 要求两个来源组均覆盖；不计入传统 Recall/MRR，也不决定来源权威。",
        "",
        "## Per-case rankings",
        "",
    ]
    for row in report["cases"]:
        lines += [
            f"### {row['id']} — {row['query']}",
            "",
            (
                f"Category: {row['category']}; acceptable rank={row['acceptable_rank']};"
                f" preferred rank={row['preferred_rank']}; old Gold "
                f"rank={row['old_gold_rank']}."
            ),
            "",
            f"Required facts: {'；'.join(row['required_facts'])}",
            "",
            f"Context: {row['query_context']}; taxonomy: {', '.join(row['taxonomy']) or 'none'}.",
            "",
            table(
                [
                    "Rank",
                    "Document#ordinal",
                    "Chunk",
                    "Score",
                    "Dense rank",
                    "Dense cosine",
                    "Sparse rank",
                    "BM25",
                    "RRF rank",
                    "Role boost",
                    "Evidence preview",
                ],
                [
                    [
                        hit["rank"],
                        f"{hit['external_key']}#{hit['ordinal']}",
                        hit["chunk_id"],
                        hit["score"],
                        hit["dense_rank"],
                        hit["dense_score"],
                        hit["sparse_rank"],
                        hit["sparse_score"],
                        hit["fused_rank"],
                        hit["metadata_boost"],
                        hit["text_preview"],
                    ]
                    for hit in row["top5"]
                ],
            ),
        ]
        if row["conflict_metrics"] is not None:
            lines += [
                table(
                    ["Source", "Claim", "Rank"],
                    [
                        [group["external_key"], group["claim"], group["rank"]]
                        for group in row["conflict_group_ranks"]
                    ],
                )
            ]
    lines += ["## Limits", "", *(f"- {item}" for item in report["limitations"]), ""]
    return "\n".join(lines)


def render_comparison(reports: dict) -> str:
    comparison = reports["comparison"]
    names = ("dense_v1", "dense_metadata", "hybrid_rrf")
    rows = {name: {row["id"]: row for row in reports[name]["cases"]} for name in names}
    attribution = comparison["v1_top1_miss_attribution"]
    a = reports["dense_v1"]
    no_answer = a["no_answer"]
    zero_fa = no_answer["max_recall_at_zero_false_accept"]
    full_recall = no_answer["min_false_accept_at_full_recall"]

    def ranks(case_id, field="acceptable_rank"):
        return " → ".join(str(rows[name][case_id][field]) for name in names)

    changes = {
        name: {
            "improved": [
                case_id
                for case_id, row in rows[name].items()
                if row["scored"]
                and (row["acceptable_rank"] or 6)
                < (rows["dense_v1"][case_id]["acceptable_rank"] or 6)
            ],
            "regressed": [
                case_id
                for case_id, row in rows[name].items()
                if row["scored"]
                and (row["acceptable_rank"] or 6)
                > (rows["dense_v1"][case_id]["acceptable_rank"] or 6)
            ],
        }
        for name in names[1:]
    }
    lines = [
        "# Multi-document Retrieval Eval v2 — Comparison",
        "",
        f"Run: {comparison['run_timestamp']}",
        "",
        "## Design and provenance",
        "",
        (
            "A/B/C 共用同一真实 Dense v1 Top-5 缓存、"
            "43 个冻结且仍 approved 的 DB chunks 和"
            " 29 个原始问题。新 Gold 只参与评分。"
        ),
        (
            "每次运行检查历史报告 SHA256、corpus/Gold/config 指纹、原始 query/Gold "
            "和数据库内容；不重新 embedding，不改变 Qdrant 或 chunker。"
        ),
        "",
        (
            f"Dense source SHA256: `{comparison['dense_source_sha256']}`;"
            f" config: `{comparison['config_fingerprint']}`."
        ),
        "",
        table(
            ["Variant", "Dense", "Metadata", "Sparse", "RRF", "Reranker"],
            [
                ["A", "v1 replay 5", "no", "no", "no", "no"],
                ["B", "v1 replay 5", "+0.05 target role", "no", "no", "no"],
                ["C", "v1 replay 5", "no", "local BM25 5", "k=60", "no"],
                ["D", "planned", "no", "planned", "planned", "SKIPPED: provider unavailable"],
            ],
        ),
        (
            "B 使用标注的 target_role 与文档 described_role；requester_role、"
            "access audience、hospital_id 不筛选候选，也无院方文档优先级。C 不含 "
            "B 的 boost，单独测 Sparse/RRF 效果。"
        ),
        "",
        (
            "BM25：NFKC/casefold、中文单字+相邻双字、ASCII 字母数字词，query term "
            "frequency=1；k1=1.2、b=0.75。title/section/text 与 v1 "
            "输入一致。"
        ),
        "",
        (
            "IDF 使用 log(1+(N-df+0.5)/(df+0.5))，参数参见 [Lucene BM25](https:"
            "//lucene.apache.org/core/9_9_1/core/org/apache/lucene/search/similarities/BM25Similarity.html)。"
            "RRF 使用 sum(1/(60+rank))，参见 [原论文](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)。"
            "并列依次按 Dense rank、Sparse rank、chunk UUID 排序。"
        ),
        "",
        "## Quality and categories",
        "",
        metrics_table([(name, reports[name]["overall"]) for name in names]),
    ]
    for category in a["categories"]:
        lines += [
            f"### {category}",
            "",
            metrics_table([(name, reports[name]["categories"][category]) for name in names]),
        ]
    lines += [
        "## Gold attribution",
        "",
        (
            f"旧 Gold Recall@1=0.76、MRR@5=0.863333；按 acceptable "
            f"重算 A：Recall@1={a['overall']['Recall@1']:.6f}、MRR@5={a['overall']['MRR@5']:.6f}。"
            f"提升来自评价修正，检索结果没有变化。"
        ),
        "",
        (
            f"旧 Top1 的 {len(attribution['original_misses'])} 个 "
            f"miss 中，{len(attribution['gold_definition'])} 个属于 "
            f"Gold 过窄：{', '.join(attribution['gold_definition'])}。"
            f"仍有排序问题：{', '.join(attribution['remaining_ranking']) or '无'}；"
            f"Top-5 candidate recall failure：{attribution['retrieval_failure_top5'] or '无'}。"
            f"不能把 Gold 修正写成模型提升。"
        ),
        "",
        "## Metadata / role cases",
        "",
        table(
            ["Case", "A acceptable/preferred", "B acceptable/preferred", "C acceptable/preferred"],
            [
                [
                    case["id"],
                    *(
                        (
                            f"{case['variants'][name]['acceptable_rank']}"
                            f"/{case['variants'][name]['preferred_rank']}"
                        )
                        for name in names
                    ),
                ]
                for case in comparison["role_cases"]
            ],
        ),
        (
            f"P01 acceptable A→B→C：{ranks('P01')}；P02：{ranks('P02')}。"
            "P01 的旧 miss 已由 Gold 修正消除；P02 要的是当前训练安排，"
            "历史训练报告和只有按钮名的说明不足以支持答案。"
        ),
        "",
        (
            f"B 相对 A 的 acceptable rank 改善：{changes['dense_metadata']['improved'] or '无'}；"
            f"退步：{changes['dense_metadata']['regressed'] or '无'}。"
            f"同角色文档同时加分，不能单凭 role boost 区分同为患者端的训练安排与报告。"
        ),
        "",
        (
            "role_disambiguation Recall@1 "
            f"A={a['categories']['role_disambiguation']['Recall@1']:.2f}、"
            f"B={reports['dense_metadata']['categories']['role_disambiguation']['Recall@1']:.2f}。"
            f"R02 医生询问患者知识、R03 患者询问医生知识，按 target_role 而非 requester_role "
            f"保留证据。结果只代表给定正确上下文时的实验。"
        ),
        "",
        "## Hybrid findings",
        "",
        (
            f"C 相对 A 的 acceptable rank 改善：{changes['hybrid_rrf']['improved'] or '无'}；"
            f"退步：{changes['hybrid_rrf']['regressed'] or '无'}。P02={ranks('P02')}；"
            f"S04={ranks('S04')}；B01={ranks('B01')}。exact_fact "
            f"见上方全指标分类表，不能用整体均值掩盖单题退步。"
        ),
        "",
        (
            f"Preferred rank A→B→C：S04={ranks('S04', 'preferred_rank')}；"
            f"D02={ranks('D02', 'preferred_rank')}；M01={ranks('M01', 'preferred_rank')}。"
            "这几题 acceptable 仍在 Top1，但优选操作说明的排序发生不同方向的变化。"
        ),
        "",
        (
            "N02 停车位预约、N03 PDF 导出仍无正文支持；Sparse 找到词汇相近片段不等于补出了答案。"
            "RRF 分数是排名融合值，不能视为 cosine 或概率，也不能沿用 A 的阈值。"
        ),
        "",
        "## Reranker",
        "",
        reports["hybrid_reranker"]["reason"],
        "",
        (
            "没有执行 D，没有可归因的 reranker 收益。Reran"
            "kerPort 保留独立接口；未来用实际 provider 评"
            "估候选重排。"
        ),
        "",
        "## No-answer / cosine separation",
        "",
        table(
            ["A class", "Min Top1", "Max Top1", "Mean"],
            [
                [name, *(no_answer[f"{name}_distribution"][key] for key in ("min", "max", "mean"))]
                for name in ("positive", "negative")
            ],
        ),
        table(
            [
                "Case",
                "A Top1 cosine",
                "A margin",
                "B Top1 boosted",
                "C Top1 RRF",
                "C Top1 document",
            ],
            [
                [
                    case_id,
                    rows["dense_v1"][case_id]["top1_score"],
                    rows["dense_v1"][case_id]["top1_top2_margin"],
                    rows["dense_metadata"][case_id]["top1_score"],
                    rows["hybrid_rrf"][case_id]["top1_score"],
                    rows["hybrid_rrf"][case_id]["top5"][0]["external_key"],
                ]
                for case_id in ("N01", "N02", "N03")
            ],
        ),
        (
            f"A 的正负分数严格可分：{no_answer['strictly_separable']}。在本样本中，"
            f"FAR=0 时最高 recall={zero_fa['recall']:.2%}（threshold={zero_fa['threshold']:.9f}）；"
            f"recall=1 时最低 FAR={full_recall['false_accept_rate']:.2%}"
            f"（threshold={full_recall['threshold']:.9f}）。"
        ),
        "",
        (
            "单一 cosine 阈值若要同时保留所有正例并拒绝所有负例，需要 max(negative)<min(positive)。"
            "当前数据不满足时，不存在这样的阈值；若接受误拒或误接，"
            "阈值仍是可测的取舍，不能直接部署。3 个负例不足以选择生产阈值。"
            "全部 25 个正例分数、3 个负例 margin 与阈值扫表见各 variant 报告/JSON；"
            "C01 不参与这个二分类。"
        ),
        "",
        "## Source conflict and duplication",
        "",
        table(
            ["Variant", "ConflictRecall@2", "@3", "@5", "Source ranks"],
            [
                [
                    name,
                    *(
                        reports[name]["source_conflict"][0]["metrics"][f"ConflictRecall@{k}"]
                        for k in (2, 3, 5)
                    ),
                    ", ".join(
                        f"{group['external_key']}={group['rank']}"
                        for group in reports[name]["source_conflict"][0]["groups"]
                    ),
                ]
                for name in names
            ],
        ),
        (
            "C01 两个来源分别给出不同患者语音挂号指令。命中双方是正确暴露冲突，"
            "不能当作普通 Top1 正确就结束；"
            "若融合丢失一方，是 conflict coverage 退步。Retriever 不裁决业务权威。"
        ),
        "",
        (
            f"跨文档完全相同 chunk 正文组数：{len(comparison['source_duplication']['groups'])}。"
            f"完整 hash 分组见 comparison.json。多份手册共享导航/楼层操作和重复 FAQ "
            f"会产生排序歧义；重复正文与同一事实的不同操作路径应区分，不能为了成绩直接删除其中一份。"
        ),
        "",
        "## Taxonomy",
        "",
        table(
            ["Class", "Definition", "A cases", "B cases", "C cases"],
            [
                [
                    label,
                    definition,
                    *(
                        ", ".join(comparison["taxonomy_cases"][name][label]) or "none"
                        for name in names
                    ),
                ]
                for label, definition in comparison["taxonomy_definitions"].items()
            ],
        ),
        (
            "Gold-definition 标签始终指原始 v1 Top1 的标注问题；source duplication "
            "表示返回了来自重复正文组的片段，不代表每次都造成错误。"
            "metadata issue 是角色题返回错误文档的线索，"
            "不等于确定根因。"
        ),
        "",
        "## Variant Delta",
        "",
        (
            "仅列 Top-5 顺序/组成发生变化，或任一 variant 仍有 acceptable/preferred/冲突覆盖问题，"
            "或尚未解决 answerability 的 case。A/B/C 字段为 acceptable/preferred "
            "rank；— 表示不适用或 Top-5 缺失，详见对应 JSON。"
        ),
        "",
        table(
            ["Case", "Top5 changed", "Unresolved", "A", "B", "C", "Top1 docs A→B→C"],
            [
                [
                    case["id"],
                    case["changed"],
                    case["unresolved"],
                    *(
                        (
                            f"{cell(case['variants'][name]['acceptable_rank'])}"
                            f"/{cell(case['variants'][name]['preferred_rank'])}"
                        )
                        for name in names
                    ),
                    " → ".join(case["variants"][name]["top1_document"] for name in names),
                ]
                for case in comparison["variant_delta"]
            ],
        ),
        (
            "comparison.json 保存上述 case 的完整 Top-5 IDs；hybrid_rrf.json "
            "另存 Dense/Sparse/RRF 整个候选并集及三种 rank。"
        ),
        "",
        "## Bottlenecks and next experiment",
        "",
        (
            "本小集最明确的问题是 Ranking（P02 的训练安排/报告语义区分）、Source Governance（C01 "
            "冲突与重复操作说明）、Answerability（无答案也能高分）。Metadata 的 target-role "
            "标注可以防止错误受众过滤，但自动上下文抽取尚未测；"
            "一次固定 boost 的结果不足以代表 metadata "
            "方法的上限。"
        ),
        "",
        (
            "下一轮优先：业务侧审阅冲突来源并明确权威/版本策略；"
            "增加独立标注的当前安排/历史报告及无答案 hard "
            "negatives，留出验证集；在固定候选集上接真实 reranker，检查 P02 和 C01 "
            "双方覆盖。若探索更深 Dense/Sparse 候选，应另开实验并保留本轮 Top-5 回放。"
        ),
        "",
        (
            "当前没有证据支持更换 embedding、调整 max_chars/overlap、删除低密度标题 "
            "chunks、把模型相关分数直接当 answerability，或因为 RRF 总分变化就宣称更可靠。"
            "A 的 Top-5 已覆盖全部普通 Gold，Embedding/Chunking 不是本集暴露的首要 "
            "recall 瓶颈。"
        ),
        "",
        (
            "hospital-specific > general、new revision > old revision、"
            "active release only 只能作为 future Knowledge Core policy "
            "候选，必须业务验证；本轮没有实施这些权威规则。"
        ),
        "",
        "## Limits",
        "",
        *(f"- {item}" for item in comparison["limitations"]),
        "",
    ]
    return "\n".join(lines)
