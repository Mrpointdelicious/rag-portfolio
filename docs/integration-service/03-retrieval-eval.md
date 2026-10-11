# 真实在线检索评测

冻结源：8 文档/15 chunks，自建 CC0-1.0；corpus `b312a323511dda3905f01b8f7c7cb6987ecf096ea17620532355e22552eb022d`；Gold `7853c39781eefe1fa0294b6a0c99842af4df9227843684e6885cdce518408dc9`；index `5dfb67dad10579dded584d9b846fe7c87da47ff1820f58e51bfa56c333cc5edd`。真实 `qwen3.7-text-embedding` 与 qwen3.7-text-rerank；无 query/rerank 缓存、无历史排名回放。final 固定题集每策略10请求=50，再加2个未存入Gold的新问题=52 HTTP请求。API调用：当前问题Embedding 42 次、真实rerank 20 次，文档索引构建另1次。

Hit=任一证据组命中；Recall=该题命中的组占比，表为按题宏平均；MRR=首个相关片段倒数排名；complete=每个组至少一个片段命中。本集每组是一个明确source-span，因此不是把“任一命中”写成“完整”。计分9题含8普通题+1预算压力（压力题与H02同问句、单列预算条件），无答案1题独立统计。K=6，ranking来自预算前selection顺序；context来自最终装包原文。延迟为客户端HTTP往返，nearest-rank p50/p95，每策略n=10，小样本p95接近最大值；BM25的0.0ms来自本机计时分辨率，不能当成零延迟或生产性能。

| 策略 | Hit@6 | Recall@6 | MRR | ranking complete | budget complete | 普通题budget complete | no-answer | p50/p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bm25 | 1.000 | 0.963 | 1.000 | 7/9 | 7/9 | 7/8 | 1/1 | 0.0/16.0 |
| dense | 1.000 | 1.000 | 1.000 | 9/9 | 8/9 | 8/8 | 1/1 | 125.0/296.0 |
| hybrid | 1.000 | 1.000 | 1.000 | 9/9 | 8/9 | 8/8 | 1/1 | 140.0/281.0 |
| hybrid_rerank | 1.000 | 1.000 | 1.000 | 9/9 | 8/9 | 8/8 | 1/1 | 281.0/656.0 |
| hybrid_selection | 1.000 | 1.000 | 1.000 | 9/9 | 8/9 | 8/8 | 1/1 | 219.0/437.0 |

所有策略超预算返回率均0/10（服务明确省略完整片段）；预算不足不意味着完成覆盖。B01 的1500字节估算预算每策略均不完整；同题10000预算可完整。BM25 在H02的Top6遗漏甲方案使用范围，真实Dense/Hybrid补齐；H04仍有“消费”词项重合，不能作为零词汇重合证明；本集 rerank/selection 没有提高最终完整题数，不能宣称普遍优于Hybrid。重排改变顺序但会消耗外部API延迟；覆盖选择与最高单片段排序目标可能冲突。

首轮 retrieval-run-1 保留了无答案N01被BM25虚词误命中的失败（4/5策略返回无关证据，Dense-only拒答）。定点增加固定通用词项支持过滤，排除仅单字/疑问词重合；没有按题编号或Gold路由。retrieval-run-2验证修复；retrieval-final补齐预算前选择顺序及provider元数据。三次原始响应全部保留，不筛选每题最好结果。单个无答案样本通过不能证明开放域拒答可靠性。

独立补充探针使用英文“How can backpressure slow a producer before its consumer is overwhelmed?”，中文原文没有任何英文词项重合；同一问题五策略各真实HTTP一次，BM25无命中、Dense/Hybrid/重排/选择均命中queue-guide@v1#flow。该1题/5请求单列，不改原Gold或合并到9题分母。另增加4次实时Embedding、2次真实rerank，原始结果见RAG lexical-probe/results.json。

两个新问题分别询问陌生提问的在线检索过程和生产/消费速度失衡，均真实调用 query Embedding、BM25/RRF、rerank、pack；Trace包含不同query-vector SHA及provider request_id，来源原文可定位。见 retrieval-final/results.json（RAG仓库），本仓库对应 metrics/provenance 摘要。

最终模型可见指标另见04；服务端TopK不可替代Agent上下文。预算估算不是模型收费token；usage可得时记录在trace，未获取计费价格、账单或完整费用，费用不估算。没有拿医学离线38/41、15/16作为在线成绩。
