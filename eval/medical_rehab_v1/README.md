# 原始 PDF 医学检索实验

领域为 `medical_knowledge`，实验为 `medical_rehab_v1`。本轮交付离线检索和可回到 PDF 的证据卡。材料只来自显式选定的 8 份原始 PDF，Word 整理稿不进入语料。

## 固定输入

| 文件 | 作用 |
| --- | --- |
| `sources.json` | 8 份原 PDF 的文件 hash、文献类型、版次、疾病/人群范围、解析与页码样本 |
| `cases.json` | 40 道原候选题，经原文技术核对后固定为 28 dev / 12 test |
| `source_reviews.json` | 逐题答案资格、必要证据组、原文 anchor、核对理由与核对人 |
| `gold.json` | 在检索前冻结的原文范围及其 hash、题集/语料指纹、测试集封存摘要 |

Gold 采用原文范围，不直接手填正确 chunk ID。原文范围再与当前冻结 chunks 映射。一个必要组内的多个范围是可替代证据，多个必要组必须全部命中才算完整覆盖。

本轮是 Codex 对来源文字与定位的技术核对，`clinical_approval=false`。40 题中，Q21–Q28 共 8 道缺少输入题单列；Q40 为文件身份核查题，单列而不计入检索质量。计分题为开发集 21 道、测试集 10 道。医学适用性与回答正确性尚未由独立医学人员验收。

## 重现流程

在仓库根目录执行。备份命令读取原盘点工作簿，校验盘点中的全部原始文件/ZIP 成员；原始路径须仍可访问。

```powershell
uv sync --locked --extra medical
uv run --locked --extra medical python tools/backup_literature.py --workbook "D:/Project/career/outputs/20261010-medical-rag-plan/02_文献盘点与试验清单.xlsx"
uv run --locked --extra medical python tools/prepare_medical_corpus.py
uv run --locked --extra medical python tools/freeze_medical_gold.py
uv run --locked --extra medical python tools/eval_experiment.py medical_rehab_v1 --split dev
```

`freeze_medical_gold.py` 只绑定已有明确核对记录，不自动完成语义标注；缺失或不唯一的 anchor 会失败。已冻结 Gold/语料若发生变化会拒绝覆盖，须另建修订并重新核对，不能借检索排名反向改 Gold。

开发配置固定后再执行封存集：

```powershell
uv run --locked --extra medical python tools/eval_experiment.py medical_rehab_v1 --split test --open-sealed-test
uv run --locked --extra medical python tools/report_medical_experiment.py
```

测试要求已有完整 dev 结果，且题集、语料、封存摘要、embedding 和检索配置完全一致。显式参数是防误运行开关；测试集不是访问控制系统。本轮测试结果已经公开，后续调参必须使用新的保留集，不能把这 12 题再次当作未见测试。

使用已有配置的真实 embedding 服务，模型/维度见实测报告；文档和 query 向量按模型 revision 独立缓存。首次运行会调用服务，重跑可复用已验证缓存。证据文本会按现有服务配置发送给 embedding provider。

## 文件与索引隔离

```text
doc/
  selected/                     8 份首批原始 PDF
  unselected/                   172 份其余 PDF，仅备份
  backup_manifest.json          原路径、ZIP 成员、Zotero 标识和全部副本校验
  backup_index.md               可点击备份目录
.local/medical_rehab_v1/
  corpus/                       原始提取、规范化逐页文本、chunks、冻结 manifest
  embeddings/                   真实文档/query 向量缓存
  qdrant/                       独立持久化本地 Qdrant
  results/dev/runs/              完整开发运行与证据卡
  results/test/runs/             完整测试运行与证据卡
artifacts/eval/medical_rehab_v1/  可公开的汇总，不含 PDF 正文和原始本机路径
```

Qdrant collection 以 `rag_eval_medical_rehab_v1_` 开头，名称绑定语料与模型指纹。每次检索同时约束领域、实验、语料、模型；证据回填再次校验 chunk/source hash 和 payload。未修改线上 PostgreSQL、原场景 collection、批准状态或活动 release。

## 检索与限制

真实 Dense 与本地 BM25 各取 20 个候选，本地 RRF 固定 `k=60`，评估 Top-5。融合保留两路候选的并集及全部排名/分数，最多 40 个，不把旧 Top-5 归档伪装成 Top-20。另比较先按显式医学条件过滤、再取候选的融合方式；不从 Gold 生成过滤条件，不推测未知人群或病程。

分块保持 800 字符、80 字符重叠，在 PDF 单页与检测到的章节内执行。字符范围指向保存的规范化页文本；原始提取文本及 hash 同时保留。PDF 页与纸面页分列，未核对的纸面页为 null。

102 页生成 736 个块；D173 PDF 第 3 页复杂表格仍有异常字形，保留备份与页文本，暂不生成检索块。未执行 OCR、Docling、reranker 或回答生成。完整来源追踪、失败归因和下一阶段门槛见 [工程说明](../../docs/medical-retrieval-experiment.md)。
