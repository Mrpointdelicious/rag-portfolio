# 独立公开医学 Gold v1

36 dev + 14 heldout；生成与封存时间、固定协议与 SHA256 见 `seal.json`。题目及原句定位由 Codex 在新检索前根据原文编排；独立人工评审 pending，clinical_approval=false。

`acceptable` 是同一事实组可接受的精确原文位置，`preferred` 是优选位置。XML offset 为规范化段落字符坐标，page=null；不是 PDF 页码。原文转载按 `artifacts/public-medical-corpus-v1/public-prose.json` 的逐文署名及 CC BY 4.0 许可保留，所有引文均来自该公开派生语料。

不得覆盖冻结 Gold。发现 heldout-14 假前提/疾病混淆被标签归入缺患者数据，已在报告披露，留待独立审阅后的新 v2 修正。参阅 [真实结果与限制](../../docs/public-medical-corpus-v1-eval.md) 和 [复现入口](../../docs/public-medical-corpus-v1-design.md)。
