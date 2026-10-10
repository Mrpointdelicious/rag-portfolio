# 医学检索第二轮（medical_rehab_v2）

本实验使用原 medical_rehab_v1 冻结的 8 份原始 PDF、102 页、736 块。全部 PDF 备份仍在 doc/selected 和 doc/unselected；Word 不进入语料。原 v1 文件、结果和元宇宙场景实验保持独立。

40 道旧题全部转为开发题（31 道计分、8 道缺输入、1 道文件身份策略题）。Q41–Q52 为运行开发实验前冻结的新保留题：10 道计分、2 道缺患者测量记录。cases.json、source_reviews.json、gold.json 通过题集指纹、原文页字符区间和测试封条绑定。临床审核状态为 false。题目由同一代理编写，保留题也来自同一语料，并非独立临床外部验证。

revision_notes.json 逐题记录题意修订；Q09 补全主要结局，Q14 标注五类局限，Q15 标注两篇各三类事实，Q18 标注两篇各目的/方法。部分原题的开放表述与原标注不一致，新版明确了问题范围。两个版本的分数不可直接比较；算法收益只在固定 v2 题目与 Gold 下比较。

source_aliases.json 只包含文献标题及人工核对的简称、中文译名。检索器不接收 Gold、candidate_sources 或题号；别名必须出现在用户问题中才路由。没有明确来源时退回基础混合检索。别名只帮助来源选择，不证明段落相关。

| 方式 | 相对比较 |
| --- | --- |
| dense / bm25 / hybrid | 同一语料与问题；Dense 20、BM25 20、RRF k=60、最终 5 块 |
| hybrid_dedup | 在 hybrid 后，仅去掉同来源同页、字符区间重合超过 90% 且文本相同的候选；保留否定和不同数值 |
| source_routed | 识别问题中的文献别名，去除已识别标题后做内容检索；每篇分别召回，再轮流取块；这是包含标题处理和来源均衡的组合策略 |
| reranked_hybrid | 将同一 hybrid 候选交给真实 qwen3.7-text-rerank 重排，保留请求标识与实际分数 |
| source_routed_rerank | 重排按来源召回的候选，再按问题中的来源顺序轮流取块 |

重排 API 使用已配置嵌入服务的同一原生地址与凭据。[官方接口说明](https://help.aliyun.com/zh/model-studio/text-rerank-api)。缓存按模型、地址、指令、问题和有序候选文本指纹绑定；外部返回必须覆盖全部候选，索引唯一、分数有限且在规定范围。失败不会用模拟分数补齐。

运行顺序：

```powershell
.venv/Scripts/python.exe tools/eval_medical_v2.py --freeze
.venv/Scripts/python.exe tools/eval_experiment.py medical_rehab_v2 --split dev
.venv/Scripts/python.exe tools/eval_experiment.py medical_rehab_v2 --split test --open-sealed-test
.venv/Scripts/python.exe tools/report_medical_v2.py
```

开发集按完整覆盖、证据组覆盖、MRR、固定方式顺序依次打破平局，冻结 selection.json；保留题要求匹配开发报告、题集、Gold、模型、配置和封条。已完成保留题不会再次打开；继续调参需新版本和新的保留题。--freeze 仅允许相同 Gold 的幂等校验。

本地结果、缓存、索引位于 .local/medical_rehab_v2。文献源和冻结语料只读复用 v1；向量缓存通过模型维度及输入指纹校验后复制，之后只写 v2；Qdrant 集合名称及每个点的 experiment_id 均为 v2。公开 artifacts/eval/medical_rehab_v2 只保存聚合指标和不含正文的诊断；完整证据卡保留在本地。

这轮未更换分块、加入 PDF、生成患者测量值、训练回答模型或校准拒答阈值。后续若做跨语言查询扩展或子问题检索，应另立版本，不能看过本轮保留题后继续报告它的“未见测试”分数。
