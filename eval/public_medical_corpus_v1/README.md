# 独立公开医学 Gold v1

36 dev + 14 heldout；生成与封存时间、固定协议与 SHA256 见 `seal.json`。题目及原句定位由 Codex 在新检索前根据原文编排；独立人工评审 pending，clinical_approval=false。

`acceptable` 是同一事实组可接受的精确原文位置，`preferred` 是优选位置。XML offset 为规范化段落字符坐标，page=null；不是 PDF 页码。原文转载按 `artifacts/public-medical-corpus-v1/public-prose.json` 的逐文署名及 CC BY 4.0 许可保留，所有引文均来自该公开派生语料。

不得覆盖冻结 Gold。发现 heldout-14 假前提/疾病混淆被标签归入缺患者数据，已在报告披露，留待独立审阅后的新 v2 修正。参阅 [真实结果与限制](../../docs/public-medical-corpus-v1-eval.md) 和 [复现入口](../../docs/public-medical-corpus-v1-design.md)。

## 固定回归与暂缓修复

2026-10-12用户决定：首轮12道未完整覆盖题全部保留，现阶段不定点修复。固定清单为 [regression_backlog.json](regression_backlog.json)，可读题目见 [待改进集合](../../docs/public-medical-corpus-v1-review-questions.md)。清单不是新Gold或新保留集；首轮Gold、seal和结果保持原样。

后续医学检索候选的回归需要覆盖原50题，并单列这12题的逐题状态。原dev/heldout仅保留来源标签，重跑结果统一称为已打开题集回归；原runner的首轮选型规则与历史报告不能用于宣称再次盲测。新入口 `tools/eval_medical_business.py regressions` 已读取固定清单、复用原评分，完成本轮50题与12题子集实跑，见[固定回归报告](../../artifacts/medical-business-v1/fixed_regression_report.json)。不能把回归解释成新保留成绩。

相同语料、锚点与预算才可直接对比原分数。改用合并库或新解析版本时，必须显式版本化锚点映射并复核，不静默替换原文位置。新迭代采用独立开发集与新冻结保留集，见 [下一步规划](../../docs/public-medical-next-iteration.md)。
