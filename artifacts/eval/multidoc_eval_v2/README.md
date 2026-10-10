# Multi-document Retrieval Eval v2

本目录保存可公开的实验说明和 [29 个 case 的 Gold 修改审阅记录](gold_changes.md)。
fixture 为 [`eval/yueyang_multidoc_v2.json`](../../../eval/yueyang_multidoc_v2.json)。
含正文片段的逐 case 报告保存在被 Git 忽略的 `.local/eval/multidoc_eval_v2/`。

## 已测结果

同一批 5 份批准文档、43 chunks、29 个原始 query，其中 25 个普通质量题、3 个 no-answer、1 个 source-conflict。
Dense 候选直接回放原先真实 DashScope/Qdrant 运行的 Top-5，使用 SHA256 固定来源；A/B/C 不会重新计算 embedding 或改变索引。
这是固定候选上的离线消融，不是新一轮在线 Dense 请求，也不测时延。

| Variant | Recall@1 | Recall@3/5 | MRR@5 | DocHit@1 | PreferredHit@1/3/5 |
|---|---:|---:|---:|---:|---|
| 历史 v1 原 Gold | 0.76 | 0.96 / 1.00 | 0.863333 | 0.92 | 不适用 |
| A: dense_v1_eval_v2_gold | 0.96 | 1.00 / 1.00 | 0.973333 | 0.96 | 0.76 / 0.96 / 1.00 |
| B: Dense + target-role boost | 0.96 | 1.00 / 1.00 | 0.973333 | 0.96 | 0.76 / 0.96 / 1.00 |
| C: Dense + BM25 + RRF | 0.96 | 1.00 / 1.00 | 0.980000 | 0.96 | 0.72 / 1.00 / 1.00 |
| D: Hybrid + reranker | SKIPPED | provider unavailable | — | — | — |

旧六个 Top1 miss 中，E01、S04、P01、M01、B01 的原 Top1 已有独立充分证据，属于 Gold 过窄。P02 仍是排序问题：A/B rank=3，C rank=2；A 的 Top-5 没有普通质量题的 evidence miss。Gold 修正的收益不能算成模型质量提升。

Metadata 对 P01/P02 的 acceptable rank 无改善，R01～R04 仍全为 rank=1。C 把 S04 preferred 从 4 提到 3，但 D02 从 1 降到 2，M01 从 2 降到 3；exact_fact、boundary_negative 的 acceptable rank 均保持 1。固定 RRF 的收益有限，不能只引用 MRR 提升。

C01 在 A/B/C 均覆盖双方：ConflictRecall@2/3/5 全为 1。另检测到 6 组跨文档完全相同的 chunk 正文，包含低密度标题与实际导航说明；重复不等于错误，更不构成擅自删 chunk 或规定来源权威的理由。

正例 Top1 cosine 区间 0.580068～0.859363；三个负例区间 0.506048～0.658479，发生重叠。单一 threshold 若保证本样本 FAR=0，最高 recall 只有 72%；若保留全部正例，最低 FAR 为 1/3。N03 的无答案检索 margin 仍达 0.142450，margin 大也不能单独证明可回答。阈值仅为本集诊断，没有部署阈值或拒答规则。

下一轮优先验证 P02 类语义重排、扩大独立 answerability 验证集、由业务侧处理来源冲突。当前没有证据支持更换 embedding、调整 chunker 或删除标题 chunks。

## 实验约定

- 保持 v1 的 `qwen3.7-text-embedding`、1024 维、`title-section-text-v1`、`simple-v1:markdown:c800:o80`、Top-K=5、audience filter=false。v1 fixture、脚本与历史报告原样保留。
- `required_facts` 定义证据需求；任一 acceptable chunk 须独立满足需求。preferred 是优选操作路径/更直接说明，不是业务权威裁定。S04/M01 按明确能到社交岛的目标等价接受不同路径，操作差异仍保留在 preferred。
- Recall@K 在本任务沿用 v1 的 hit-rate 定义：Top-K 有任一 acceptable 即为 1；MRR 按首个 acceptable 计算。PreferredHit 独立计算；DocHit 使用 acceptable document keys。
- C01 使用两个 conflict source group，双方各至少一个证据进入 Top-K 才为 1。它不进入普通 Recall/MRR 或 answerability 二分类分母。
- B 在 Dense Top-5 内给 `target_role == described_role` 的候选加 0.05；固定 boost 在运行前选定，未经调参。上下文是评测标注，未测试自动角色意图识别。R02/R03 的 requester 和 target 不同，不能用 requester 或 access audience 排除目标知识。
- C 不叠加 B。Sparse 在同 43 个 chunks 的 title/section/text 上取 Top-5，与 Dense Top-5 合并。候选并集至多 10，最终 Top-5。更深候选需另开实验，不在本轮偷偷扩大。
- 本地 BM25 仅为 eval implementation：NFKC + casefold；中文单字/双字，ASCII 字母数字词；query term frequency 为 binary；不加同义词/停用词词典。`k1=1.2, b=0.75`，IDF 为 `log(1+(N-df+0.5)/(df+0.5))`，参考 [Lucene BM25](https://lucene.apache.org/core/9_9_1/core/org/apache/lucene/search/similarities/BM25Similarity.html)。
- RRF 为 `sum(1/(60+rank))`，固定 `k=60`，参考 [RRF 论文](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)。相同融合分数依次比较 Dense rank、Sparse rank、UUID；不借 Gold 打破并列。
- `DenseRetriever`、`SparseRetriever`、纯函数 Fusion、`RerankerPort` 位于 evaluation 模块，与 Qdrant adapter 解耦。未找到已配置的 reranker；D 不构造假指标。
- A cosine、B boost 后分数、C RRF 分数不可混用。各自输出 25 个正例分数、3 个负例分数/margin 和完整阈值扫表；precision/recall 衡量“是否有答案”标签，不等同于答案正确率。

## 重复运行

在仓库根目录、有已批准的原始冻结数据库和 v1 本地历史报告时执行：

```powershell
uv run python tools/eval_retrieval_v2.py
```

显式路径等价命令：

```powershell
uv run python tools/eval_retrieval_v2.py --suite eval/yueyang_multidoc_v2.json --corpus .local/eval/hospital_dense_v1/corpus.json --dense-source .local/eval/hospital_dense_v1/runs/20261004T122146548950Z.json --output-dir .local/eval/multidoc_eval_v2
```

运行只读取 PostgreSQL，用既有 frozen-corpus 校验重新验证版本批准状态、scope、metadata 和 chunk 正文 hash；发现漂移立即失败。无需重新调用 embedding API、重建 Qdrant 或批准文档。普通单元测试用合成数据，不依赖私有语料或外部模型。

公开仓库不携带真实业务正文或原始 Dense 缓存。另一台机器需要通过授权渠道获得匹配的本地数据与历史报告；缺少时明确失败，不能用新生成的报告伪装同一个冻结基线。Dense source SHA256 固定为 `670be4ed6b45e01068cbcda43051fb858e62cd1c35e6cf81776576fd1331e234`。

每次完成会保存不可覆盖的 `runs/<UTC timestamp>/`，并更新当前报告：

```text
.local/eval/multidoc_eval_v2/
  dense_v1.json / .md
  dense_metadata.json / .md
  hybrid_rrf.json / .md
  hybrid_reranker.json / .md
  comparison.json / .md
  latest_run.json
  runs/<UTC timestamp>/...
```

每份 A/B/C 报告包含 overall、7 个普通类别、no-answer、source conflict 和完整 29 个 case 的排名。C 另含 Dense/Sparse/RRF ranks、原分数和候选并集。D 保存 29 个 case 的 skipped 状态。comparison 记录所有变化/仍有问题的 case、12 个角色题、旧 Gold 归因与 7 类问题标签。报告含正文片段，应继续保存在 `.local/`。

核验命令：

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
uv run --locked python tools/test.py -q
```

本地验证于 2026-10-05（Asia/Shanghai）完成：Ruff 检查与格式检查通过；普通 pytest 为 133 passed / 33 skipped；隔离 PostgreSQL 的完整测试为 166 passed，包含新增 35 项 v2 测试，临时测试库已清理。

两次离线运行归档为 `20261004T184050419434Z` 和 `20261004T184807416738Z`（UTC）。五份 JSON 在去除本次运行时间后完全一致，包括全部候选分数/排名、Gold 评分、阈值结果与 D 的跳过记录。原 v1 的 17 个快照/fixture/脚本/报告/历史说明文件 SHA256 全部未变；本地审计结果见 `.local/eval/multidoc_eval_v2/verification.json`。

本轮不修改生产 Core、Document/Version/Review 语义，不实施 hospital-specific > general 等 authority policy，不接 Answer Generation 或正式 Release。
