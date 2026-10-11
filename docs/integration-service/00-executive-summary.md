# 隔离 Hybrid 集成验收

完成独立只读检索服务与 MetaAgent 集成，达到本轮最小工程闭环；答案语义仍为 **PARTIAL**，临床正确性未评估。

| 链路 | 本轮真实结果 |
|---|---|
| Dense / BM25 / RRF | 新问题实时 Embedding、静态向量余弦检索、全文 BM25、RRF 均已运行 |
| Reranker / Context | 真实 Qwen 重排；完整原文预算打包；最终比较证据 6/6，压力用例 4/6 |
| Agent / Docker | 真实模型、HTTP、Evidence、FactView、引用；容器构建、健康、DNS/TCP、回答与停止清理均实测 |
| 回归 | 隔离 PostgreSQL：RAG 252 passed；MetaAgent 305 passed；均无 skipped |

固定题集 8 道普通有证据题、1 道预算压力题、1 道无答案题，五策略各真实运行一次，另有 2 个新问题。完整预算内覆盖 BM25 7/9，其余四种 8/9；小样本不代表业务普遍效果。

E02/E09 各固定条件复跑 3 次；E02 状态聚合与同查询恢复已实测，E09 有 1/3 次补充原文未支持的医学解释。N01、P01 仍有越界说明/能力承诺。合法引用不等于语义充分。

代码提交：RAG `e05403fb`，MetaAgent `7e456c78`；专用分支 `codex/hybrid-retrieval-service`，未推送或合并。旧 demo、正式 /v1/retrieve 501、默认 local、原 .env/生产服务保持原行为。

外部模型可用。Docker 初次不可用后恢复并验收；PostgreSQL16 镜像拉取超时，改用本地15-alpine独立容器完成。本轮依赖均已具备；PG16下载故障未解决，PG15验收不代表已验证PG16。**唯一优先后续：补齐 E09/N01/P01 无证据解释与能力承诺的业务回归门槛。**
