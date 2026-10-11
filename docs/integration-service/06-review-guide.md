# 人类 Review：五个问题

1. **为什么HTTP独立？故障在哪层结束？** 看RAG `api/hybrid.py:create_app` 与 `services/hybrid_retrieval.py:HybridPipeline.search`、MetaAgent `domains/remote_hybrid.py:retrieve/HybridKnowledgeAdapter.execute`。索引与provider依赖隔离于Agent图，网络/服务故障有独立契约；不改Agent运行框架。
2. **五个阶段分别优化什么，指标会冲突吗？** Dense解决表达改写；BM25保留精确词；`reciprocal_rank_fusion`合并不可直接比较的分数；Reranker对当前候选重排；`coverage_order`与atomic pack追求来源/问题维度和预算。最高单片段相关性不一定完整多来源，重排改善MRR不保证complete，字节预算还可能省略整段。看03的H02/B01及独立英文探针原始响应，不看离线医学成绩。
3. **一个句子如何追到原文？** `KnowledgeAdapter`分发→HybridAdapter验证→repository.save_evidence（Evidence ID）→`FactBuilder.add`（FactView.fact.record_ref=doc_ref、path=/snippets/i/text）→`agent/tools.py:invoke_tool`→`agent/context.py:compact_knowledge`→最后模型ToolMessage。Evidence ID指存储事件，doc_ref指文档版本/section，chunk ID指稳定源片段；别混成一个ID。04逐句表与raw Evidence提供start/end/hash定位。
4. **合法引用为什么还会解释越界？** `knowledge_citations.py:audit_remote_answer`只验证存在/新鲜/完整性及最终可见成员，不能证明句意。E09-1证据里只有指标名称，模型却补医学分类；证据完整可见，原因是生成补充常识。N01无证据仍推测名称真假；P01缺能力仍承诺查询。看sentence-review.md中not-in-evidence/unverifiable，不用citation通过冒充语义/临床正确。
5. **什么时候partial/unavailable/failed？恢复怎样算？** 看`agent/runtime.py:SingleAgentRuntime.execute`与tests/test_remote_hybrid.py。按当前run、tool+query/domain最后结果聚合：不同请求有成功有失败→partial；同请求重试恢复→succeeded；没有可用证据→unavailable；当前有效来源完整性失败或最终引用审计失败→failed。重排降级/上下文缺失→partial或无完整片段时unavailable。检查真实S01/F02/F03及B01，而非只看Mock断言。

两个取舍：静态index+pin减少演示基础设施，但不提供正式发布/切换/重建一致性；UTF-8字节预算可复现且保守，但低估可装入执行模型的片段数，B01覆盖会损失。这两个限制写入契约，不能当生产成熟度宣传。

唯一最高优先后续：冻结E09/N01/P01更多独立反例，建立无证据解释和能力承诺的业务发布回归门槛；不做通用“语义保证器”。
