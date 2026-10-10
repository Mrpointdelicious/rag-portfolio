# 求职作品开发路线

定位：展示通用 RAG 系统的完整工程链路。默认演示素材为自编项目文档、技术 FAQ 和有使用许可的公开资料；先固定一套小型可重复的语料，再扩展规模。

| 阶段 | 状态 | 主要交付 |
|---|---|---|
| P0 基础工程 | 完成 | 独立包名、Git、凭据、端口和数据卷，迁移、鉴权、健康检查、持久任务 |
| P1 最小内容治理闭环 | 完成 | Markdown/TXT 导入、Document/Version、hash、人工审核、approved 后持久化 chunks |
| P2-A Dense Retrieval baseline | 完成 | DashScope → Qdrant → PostgreSQL hydrate，单文档 10 chunks / 6 queries |
| P2-B Multi-document Dense Evaluation | 完成 | 5 文档 / 43 chunks / 29 queries，两次真实运行；多 Gold、类别指标、失败与无答案观察 |
| P2-C Sparse + RRF | eval-only 对照完成 | v2 acceptable/preferred Gold、Metadata boost、本地 BM25/RRF、no-answer/conflict 指标 |
| P2-D Reranker | SKIPPED：provider unavailable | RerankerPort 已定义，等待真实 provider 后评估 |
| P2-E Release + retrieve API | 未开始 | 完整索引发布、激活、撤销、回滚与检索接口 |
| P3 引用问答与演示 | 未开始 | LLM、证据引用、未知问题处理、演示界面 |
| P4 扩展评测与工程验证 | 未开始 | 更大评测集、消融、故障注入、恢复及监控 |
| P5 作品整理 | 持续维护 | 架构、设计取舍与可核验实验记录 |
| M1 医学 PDF 独立实验 | 离线首轮完成 | 180 份 PDF 备份、8 份/102 页/736 块、40 题技术 Gold、独立真实 Dense/BM25/RRF 与证据卡 |
| M2 医学检索改进 | 离线第二轮完成 | v2 技术 Gold 修订、40 开发题/12 新保留题、真实重排与路由对照；保留首轮结果 |
| M3 医学问题拆解与证据预算 | 离线第三轮完成 | 统一事实复核、52 开发题/20 新保留题、七组对照、Top-5 与统一长度预算、缺输入路由 |

P1 完成的是最小内容治理闭环，见 [调用约定](document-flow.md)；通用模型迁移、HTTP 上传和长任务仍未实现。
P2-A 历史结果见 [Dense baseline](dense-retrieval-baseline.md)，保持原始六个 query、Gold 与历史指标。
P2-B 约定见 [多文档评测](multi-document-dense-eval-v1.md)。不将来源项目的临床质量目标或内部系统联调记录作为本项目成果。
P2-C 结果与重跑命令见 [Retrieval Eval v2](../artifacts/eval/multidoc_eval_v2/README.md)。以同一真实 Dense Top-5 做离线消融，原始 v1 artifact 不变。

## 医学实验的后续门槛

[医学首轮记录](medical-retrieval-experiment.md)与[冻结结果](../artifacts/eval/medical_rehab_v1/README.md)独立于元宇宙场景。首轮测试 Hybrid Hit@5=0.60，但完整覆盖=0.40、多篇题完整覆盖=0/2，结果保留。

[第二轮](../artifacts/eval/medical_rehab_v2/README.md)修订技术 Gold，在开发运行前冻结新保留题并实跑重排。固定 v2 题集下开发完整覆盖 Hybrid 13/31、选定组合 21/31；新保留题分别为 8/10 和 10/10。复杂多篇开发题仍仅完整覆盖 2/6，不能据此进入临床问答。下一轮优先检验标题语义保留、逐来源子问题召回、分块边界及证据预算；来源均衡与去重没有稳定收益。跨语言查询扩展若继续试验，需要新实验版本与新保留题，不重复使用已打开的测试集。

[第三轮](../artifacts/eval/medical_rehab_v3/README.md)已完成上述方向的七组对照。固定 v3 Gold 与 4000 参考 token 预算内，普通重排开发完整覆盖 33/41，选定方案 38/41；新保留题分别为 12/16、15/16。旧版路由重排组合保留题也是 15/16，新方案没有预算指标优势。多篇保留题仍有 1 道证据已进入 Top-5、打包时却遗漏的失败；扩展上下文也没有保留题额外收益。

医学下一阶段先比较预算内来源/维度配额和句子级原文片段选择，保留年龄、否定、单位及页码；结合开发 Q15/Q31 检查维度识别与人群证据选择。继续使用现有 PDF 诊断检索链路，只有确认知识确实缺失才补资料。新迭代要另冻结新保留题，并在进入回答生成前取得独立医学事实核验；本轮已打开题不再作为未见测试。

[患者实测数据核查](patient-measurement-data-audit.md)发现历史报表和接口候选，但没有本次可直接使用的已核验测量测试集；当前数据库未在线核验。之后取得可靠结构化记录，应独立测试身份、时间、单位、原始/派生状态与真值，不把患者值写入文献 Gold。

正式 retrieve/answer 继续沿用人工批准、tenant、知识库、版本、release 与持久页码映射要求。离线实验不自动批准文献或激活发布，医学人员验收尚未完成。

## 元宇宙场景的后续任务

1. 由业务侧审阅 C01 的冲突来源和重复内容，明确未来 Knowledge Core 的权威/版本策略；Retriever 已能暴露双方，不擅自决定谁优先。
2. 保留 P02 的“当前训练安排”与“历史训练报告”混淆，接真实 reranker 后在固定候选上验证。Metadata boost 本轮没有改善，Hybrid 只将 rank 3 提至 2；不据此更换 embedding 或 chunker。
3. 扩大独立标注的 hard negatives 和 answerability 验证集。现有 3 个 no-answer 与正例 cosine 区间重叠，不从小样本直接部署单一阈值。
4. 后续另行规划通用 KB/受众模型、HTTP 上传、长任务租约与 Release 发布，不混入当前评测。

## 评测与展示约定

- 同一语料、问题集、模型 revision 和资源配置下对比实验，记录 corpus/config hash。
- 检索评估可采用 Recall@k、MRR/nDCG；回答评估记录引用正确性、证据支持度和未知问题处理。阈值在首轮基准后设定，不预填实验收益。
- 每阶段交付可执行命令、演示输入、预期输出与失败样例。
- 项目经历区分“继承基础”“本人新增设计与实现”“实测结果”，便于面试时准确说明贡献。
- Dify 导入与场景导航不作为通用作品首轮必需功能，可在核心 RAG 链路完成后选择扩展。
