# Multi-document Dense Evaluation v1

本实验在单文档 baseline 之外，固定五份同 tenant、同 KB 的批准版本，评估跨文档干扰、角色混淆、
重复证据和无答案问题。旧 `tools/retrieval_smoke.py`、六个历史 query、Gold、结果和 collection 保持不变。

## Corpus 与人工审核

| external_key | Audience | 适用范围 |
| --- | --- | --- |
| hospital-scene-guide | patient + doctor | 岳阳；固定 revision 2，10 chunks |
| general-patient-guide | patient | global |
| general-doctor-guide | doctor | global |
| yueyang-patient-guide | patient | yueyang |
| yueyang-doctor-guide | doctor | yueyang |

原始 Markdown 由用户放在 `data/eval_sources/yueyang/`。不得补写缺失正文、修改事实、去重、合并角色文档
或静默修正通用患者端的语音挂号冲突。本轮没有改写源文件。

复用现有 importer。其 schema 要求 `manifest.json` 同级的 `documents/` 存放输入文件，因此将四份
Markdown 按字节复制到 `data/eval_sources/yueyang/documents/`，原文件保留。整个目录仍被 Git 忽略。
本地 `manifest.json` 使用原 hospital 文档的 KB、tenant、source_instance_id；每份文档分别记录 audience，
`metadata.applicability_scope` 为 global/yueyang，不改变 KB 的 shared scope。

```powershell
uv run python tools/import_documents.py validate data/eval_sources/yueyang/manifest.json
uv run python tools/import_documents.py import data/eval_sources/yueyang/manifest.json
uv run python tools/import_documents.py status data/eval_sources/yueyang/manifest.json
uv run python tools/manual_approve_version.py <version-id>
```

最后一条对四个导入版本分别由用户执行，阅读完整正文后手工输入 `APPROVE`。导入器只到 pending；
评测工具不会批准、重新生成 chunk 或修改业务数据。导入结果中的具体版本 UUID 在本地 `import_report.json`。

## 冻结与 drift

```powershell
uv run python tools/freeze_eval_corpus.py
# 可选参数：
uv run python tools/freeze_eval_corpus.py --help
```

默认读取 `data/eval_sources/yueyang/import_report.json` 的四个明确 version UUID，并加上固定
`cc725d7d-f8a9-455d-a613-3ccf71705fae`；不按“最新版”自动选择版本。
`--import-report` 可显式指定报告，`--output` 默认 `.local/eval/hospital_dense_v1/corpus.json`。
已有 snapshot 只允许内容完全相同时复用，拒绝覆盖不同 snapshot。

验证五个 external_key/文档/version 唯一、同 tenant/KB、approved、未撤销、有 chunks、每个版本只有
一个 `simple-v1:markdown:c800:o80` chunker；hospital 必须为原 UUID、revision 2、10 chunks。
重新计算 version 正文/元数据 hash 和 chunk 正文 hash，不能只信任数据库里保存的 hash 字段。

快照保存 ID、revision、title、audiences、source_instance_id、scope、hash、chunk ordinal、section_path，
不保存完整正文。每次评测开始、embedding 完成后、每次 hydration 及结束时都重新读库校验。
标题、章节、chunk 集合或正文、元数据、归属、批准/撤销状态变化即失败，不换新版继续跑。
小 corpus 的 hydration 使用两次批量 SQL 加 KB 校验，重新读取全部五个版本与 chunks，再按 Qdrant ID
恢复排名顺序；这也能发现当前 Top-5 之外的文档发生 drift。

## 检索与运行

固定 `alibaba_dashscope:qwen3.7-text-embedding:d1024:dense:title-section-text-v1`，
document/query 分别使用对应 `text_type`，输入仍为 title + section_path + text，空段剔除、标题不去重。
`BATCH_SIZE=20` 集中定义在 evaluation 模块；按顺序逐批 embedding，所有批次成功后才创建/重建 collection
并 upsert。任何一批失败都终止，不发布完整成功报告。原 `embed_documents()` 语义未改变。

```powershell
uv run python tools/eval_retrieval.py `
  --suite eval/hospital_dense_v1/cases.json `
  --corpus .local/eval/hospital_dense_v1/corpus.json `
  --recreate

uv run python tools/eval_retrieval.py `
  --suite eval/hospital_dense_v1/cases.json `
  --corpus .local/eval/hospital_dense_v1/corpus.json
```

参数均有默认值与 `--help`。`--output-dir` 默认 `.local/eval/hospital_dense_v1`。
只使用隔离 collection `rag_eval_yueyang_dense_v1`，1024 维 COSINE，Top-K=5，无 audience filter、无阈值。
point ID 为 chunk UUID，payload 沿用 baseline 的定位与验证字段，不保存正文。检索限制在 snapshot 的
chunk ID、tenant/KB 和 embedding revision 范围内。索引后检查整个 collection 的 ID 集合和 payload，
必须与 frozen chunks 完全一致；单纯 point count 相等并不足以成功。

既有 collection 仅允许同 revision 且所有现有 ID/payload 都属于当前快照，允许恢复未完成的部分索引。
upsert 使用 `wait=True`，写入异常或验证不完整时本次失败；不把残余 points 当作完整成功索引。
本工具是单进程评测工具，运行时不要有其他进程并发改写同一 eval collection。

## Cases、Gold 与指标

`eval/hospital_dense_v1/cases.json` 固定 29 个原始 query。Gold 在首次真实评测之前按用户提供的 Markdown
章节和批准后的 chunk 映射，评测结果不能反过来改变 query 或 Gold。
每条 Gold ref 通过 external_key + section_contains + 可选 text_contains 唯一解析；0 或多个匹配都失败。
多证据用多个显式 ref 表达，不把歧义匹配自动扩成 Gold。

- 25 个 scored case：exact_fact 6、semantic_paraphrase 4、patient_role 4、doctor_role 4、
  role_disambiguation 4、multi_gold 2、boundary_negative 1。
- N01–N03：answerable=false、scored=false，仍返回 Top-5，记录最高分/文档/章节，不作阈值拒答。
- C01：answerable=true、scored=false，Gold refs 指向两份患者文档的挂号段落；完整 Top-5 标记
  `SOURCE_CONFLICT` 和 `excluded_from_quality_metrics`，不裁定业务事实。
- P04 依据患者文档的手动挂号流程，不以冲突语音指令判定相关性。
- B01 是可回答的边界问题，仍以“五层空间”的事实块为 Gold。

对 scored 且 answerable 的 case 计算 Recall@1/@3/@5、MRR@5、DocHit@1，再按整体和七个类别取均值。
这里 Recall 按任务约定指 **任意 Gold 命中率**，不是已召回 Gold 数 / Gold 总数；MRR 取第一个 Gold rank，
未入 Top-5 为 0；DocHit@1 检查第一条 evidence 文档是否属于任一 Gold 文档。

失败分类可以重叠：Gold not in Top-5、Gold rank 2–5、Top1 wrong document、
Role-disambiguation Top1 wrong role document。最后一类要求 Top1 文档的 audience 与 Gold evidence 的
audience 不相交；R02 的 Gold 是患者限制说明，R03 是医生限制说明，不直接用提问者自称角色做判断。

## 本地报告

完成全部 29 个 query、DB drift 和完整索引校验后才写 `latest.json` 与 `latest.md`。
另在 `runs/<UTC timestamp>.json/.md` 保留每次完整运行，以比较两次排名和 point count。
失败不会把旧 latest 冒充本次成功：CLI 非零退出并明确提示本次未发布完整报告；旧报告时间戳保持不变。

报告包含 corpus/suite fingerprint、版本摘要、运行配置、overall/per-category、所有 case 的 Gold IDs/rank、
Top-5 score/文档/ID/ordinal/section/text preview，以及 C01 全文 evidence。
JSON 不保存 API Key、配置对象或请求头。`data/`、`.local/`、`.env` 不提交 Git；公开内容仅为通用代码、
query/Gold 引用、合成测试与不含完整业务正文的实验说明。

## 验证

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python tools/test.py -q
```

测试不调用 DashScope。使用合成 corpus、mock embedding、Qdrant client 内存实现和现有隔离 PostgreSQL runner，
覆盖 snapshot 边界/drift、多版本 hydration、Gold 歧义、multi-Gold、指标排除、分批失败和报告输出。
Sparse/BM25、RRF、reranker、LLM、Release、worker、retrieve API、chunker v2 均未纳入本轮。

## 2026-10-04 真实运行结果

四份新增文档经用户执行原有人工脚本批准后冻结。tenant 为 portfolio，KB scope 为 shared。

| external_key | version UUID | revision | chunks |
| --- | --- | ---: | ---: |
| hospital-scene-guide | cc725d7d-f8a9-455d-a613-3ccf71705fae | 2 | 10 |
| general-patient-guide | f729a67e-7a40-4ab9-b8d7-bd1d6875fc1d | 1 | 6 |
| general-doctor-guide | f162c021-e94b-42d6-87e0-92afab21246e | 1 | 3 |
| yueyang-patient-guide | 56aed616-1927-48e0-baf7-840f36215404 | 1 | 12 |
| yueyang-doctor-guide | 7dece257-1239-46e2-8d4b-0ad5244454bc | 1 | 12 |

总计 43 chunks，document embedding 请求为 20 / 20 / 3。两轮分别于 UTC 12:21:02、12:21:46 开始，
第一轮 recreate，第二轮复用 collection，均成功完成。point count 都为 43；全部 29 个 query 的 Top-5
chunk ID 排序、Gold rank、overall/per-category 指标一致。两轮匹配 point 的 score 最大差异约 0.000352，
不声称供应商输出可逐位复现。旧 smoke collection 未用于此实验。

25 个计分 case 的指标如下；3 个 no-answer 与 C01 均排除：

| Category | Count | Recall@1 | Recall@3 | Recall@5 | MRR@5 | DocHit@1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Overall | 25 | 0.760000 | 0.960000 | 1.000000 | 0.863333 | 0.920000 |
| exact_fact | 6 | 0.833333 | 1.000000 | 1.000000 | 0.916667 | 1.000000 |
| semantic_paraphrase | 4 | 0.750000 | 0.750000 | 1.000000 | 0.812500 | 0.750000 |
| patient_role | 4 | 0.500000 | 1.000000 | 1.000000 | 0.708333 | 0.750000 |
| doctor_role | 4 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| role_disambiguation | 4 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| multi_gold | 2 | 0.500000 | 1.000000 | 1.000000 | 0.750000 | 1.000000 |
| boundary_negative | 1 | 0.000000 | 1.000000 | 1.000000 | 0.500000 | 1.000000 |

### Failures / Near misses

| Case | Gold rank | 首位结果与解释 |
| --- | ---: | --- |
| E01 | 2 | hospital ordinal 9 的 FAQ 同样回答层数；固定 Gold 为布局事实块 ordinal 4 |
| S04 | 4 | 岳阳患者端快速通道排第一，患者/医生的场景传送随后；固定 Gold 为 hospital 返回按钮 |
| P01 | 2 | 患者端按钮详细介绍排第一，也包含右上角两按钮的 FAQ；Gold 是按键分布段 |
| P02 | 3 | 通用患者端“就诊记录与训练报告查看”排第一，混淆当前训练安排与历史报告 |
| M01 | 2 | 患者端快速通道排第一且提及社交岛；三个固定 Gold 是返回按钮与两份场景传送段 |
| B01 | 2 | hospital ordinal 9 的 FAQ 排第一，固定 Gold 仍是“五层空间”布局块 |

Gold miss Top-5：无。Top1 wrong document：S04、P02。
role_disambiguation 四个 case 全部 Gold rank=1，无该类别的 Top1 wrong role document。
这是四个固定角色消歧问题上的观察，不能推断所有角色混淆均已解决。

### No-answer 与来源冲突

以下为第二轮 score，仅作为观察，不设置阈值或生成业务答案：

| Case / query | Top1 score | Top1 document / section |
| --- | ---: | --- |
| N01 怎么修改登录密码？ | 0.554944 | general-patient-guide / 返回登录界面 |
| N02 岳阳医院停车位怎么预约？ | 0.658479 | yueyang-patient-guide / 患者就诊挂号 |
| N03 怎么把训练报告导出成 PDF？ | 0.506048 | general-patient-guide / 就诊记录与训练报告查看 |

C01 的完整 Top-5 正文仅保存在本地报告，这里记录定位摘要：

| Rank | Score | external_key | ordinal | section |
| --- | ---: | --- | ---: | --- |
| 1 | 0.736931 | yueyang-patient-guide | 9 | 患者就诊挂号 |
| 2 | 0.730910 | general-patient-guide | 4 | 患者就诊挂号 |
| 3 | 0.637094 | general-doctor-guide | 2 | 快速前往问诊 |
| 4 | 0.636213 | yueyang-patient-guide | 4 | 按钮功能详细介绍 |
| 5 | 0.624487 | yueyang-patient-guide | 3 | 屏幕按键功能分布与介绍 |

前两项展示了用户指出的不同挂号语音说明，按 `SOURCE_CONFLICT / excluded_from_quality_metrics` 处理，
没有由代码决定哪条说明正确，也没有合并或修正文档。

### 验证与下一步

`ruff check .`、`ruff format --check .` 通过；`pytest -q` 为 98 passed / 33 skipped；
`tools/test.py -q` 为 131 passed，迁移升级/降级/差异检查通过，临时测试库已清理。
现有两条依赖弃用告警及本地 Qdrant HTTP + API Key 提示仍存在，没有运行失败。

两份完整运行存档是本地 `runs/20261004T122102565400Z.json` 和
`runs/20261004T122146548950Z.json`，同名 Markdown 及 latest 文件位于本实验输出目录。

建议先人工审阅 **corpus / Gold**：确认 C01 的业务来源；复核 E01/S04/P01/M01/B01 的重复有效证据是否
需要在未来独立版本中补充标注。保留本轮 query、Gold、chunker 和所有失败，不回填 v1 成绩。
P02 是需要保留的真实语义混淆回归例；待标注审阅后再决定 embedding input 或 Sparse + RRF 的对照实验，
目前这组结果不足以预设必须增加组件。本轮未做自动调参或后续组件实现。
