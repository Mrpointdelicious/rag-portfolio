# Agent 真实验收与逐句核查

模型 deepseek:deepseek-chat，temperature=0，固定 business-grounded-v9、max8模型调用/run；没有新增提示词版本。公开合成语料与隔离TCP服务；故障场景是实际HTTP上的显式注入，正常检索不使用Mock。MockHTTP与脚本模型只用于契约反例，pytest成绩不混入真实模型指标。

共保存 41 个真实本机Agent run / 81 次模型请求（含诊断失败）；另Docker真实回答见05。逐句审查覆盖25个run：原E02/E09六次、域提示修复后的16次、完整性/最终可见引用修复后三次定点复测。effective表22场景，最后一次定点复测覆盖其同名场景；没有删除之前失败。所有原始 model_inputs/model_outputs/raw_final/answer、Tool参数/结果/码、Evidence全文、来源范围/hash、retrieval_run_id、status和events均保留。

| Case | 实际采用的run | Run状态 | 最终可见refs | 必要证据遗漏 | 语义核查 |
|---|---|---|---:|---|---|
| E02-1 | agent-run-1 | succeeded | 6 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| E09-1 | agent-run-1 | succeeded | 2 | 无/不适用 | PARTIAL |
| E02-2 | agent-run-1 | succeeded | 6 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| E09-2 | agent-run-1 | succeeded | 2 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| E02-3 | agent-run-1 | succeeded | 6 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| E09-3 | agent-run-1 | succeeded | 2 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| H01 | agent-run-2 | succeeded | 6 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| H02 | agent-focused-integrity | succeeded | 7 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| H03 | agent-run-2 | succeeded | 5 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| H04 | agent-run-2 | succeeded | 2 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| N01 | agent-run-2 | unavailable | 0 | 无/不适用 | PARTIAL |
| P01 | agent-run-2 | succeeded | 0 | 无/不适用 | PARTIAL |
| U01 | agent-run-2 | succeeded | 7 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| B01 | agent-focused-integrity | partial | 4 | index-a@v1#outcomes, index-b@v1#outcomes | SUPPORTED_WITHIN_REVIEW_SCOPE |
| T01 | agent-run-2 | succeeded | 6 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| T02-expired | agent-run-2 | succeeded | 6 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| S01 | agent-run-2 | partial | 6 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| F01-timeout | agent-run-2 | unavailable | 0 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| F02-retry | agent-run-2 | succeeded | 6 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| F03-tamper | agent-focused-integrity | failed | 0 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| F04-corpus-drift | agent-run-2 | unavailable | 0 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |
| F05-index-drift | agent-run-2 | unavailable | 0 | 无/不适用 | SUPPORTED_WITHIN_REVIEW_SCOPE |

最终来源引用成员 53/53（每个effective回答内去重计数）；这不是句子准确率。逐句记录 {'supported': 224, 'not-in-evidence': 2, 'unverifiable': 3}，包含运行/缺失说明，非临床评测。审查由本轮助手根据保存原文进行，供人类逐条核查；尚无独立人工复评。见 sentence-review.md、review.json；行内没引用的句子明确指向source_review_pool/运行事件，不冒称精准归因。

E02固定原问题各3次均取得两来源三维度，run均succeeded，来源可见6/6。S01真实不同域查询：有效证据+域拒绝为partial。F02首请求HTTP503，同一query/domain重试成功，run succeeded；契约测试另保留不同查询nohit+成功为partial及同请求恢复反例。只聚合当前run的最后有效调用，历史故障不污染新run。

E09固定top_k=1各3次，模型分别查询两来源取回两段结果，工程状态3/3 succeeded；第1次仍增加“步行耐力/运动能力”“平衡功能方向”等来源没有的解释，因此语义 **1/3 PARTIAL**，不能宣称P0全面修复。全部原始回答保留。E02未发现相同新增解释不等于保证未来不会出现。

H02最终模型可见必需片段6/6，服务端→Evidence→FactView→ToolMessage链路可回指原文。B01服务端每次仅打包2片段，Agent两次调用最终4/6，缺两个计划指标；回答明确缺失，run partial。初始512 Toolkit预算甚至0完整片段能进入，记录为unavailable；1600预算试验保留该差异。T01有效证据跨轮复用；T02删除本次隔离Evidence模拟TTL后重新检索，不复用失效引用。新Hybrid最终审计将引用限制到最后一次模型调用实际可见的ToolMessage facts，单元反例阻止“存了但没看见”的来源被引用。

N01正确无证据拒答后仍添加“名称看起来不真实”的推测；P01拒绝个人值后仍承诺可按日期/设备查记录，当前无可信身份时不能兑现；这两项继续PARTIAL，未靠提示词反复调参追分。H01/H02/H04还存在补充无关但有来源内容的问题，相关性需要更独立的业务集。

F01超时与nohit分开；F03验签失败拒用原文，最后定点复测run **failed**（此前unavailable的旧结果保留）；F04/F05临时独立copy的corpus/index漂移为unavailable，恢复后health正常。正式患者服务与数据未接入。真实拒绝、检索正确、引用有效、语义充分、临床正确五个结论独立。

运行样本：22个effective场景的Run状态为15 succeeded / 2 partial / 4 unavailable / 1 failed，其中负向场景的不可用/失败是预期，不当作回答错误率。Agent整轮延迟p50=2828.0ms、p95=4594.0ms，n=22、nearest-rank，包含故障注入，不能与纯检索混比。41个本机run的81次真实模型请求报告input=231319、output=11545、total=242864 tokens；价格/费用未获取。
