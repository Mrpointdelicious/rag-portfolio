# retrieval-v1 HTTP 契约

新端点 `POST /v1/experimental/hybrid/retrieve` 在独立 launcher 上运行；旧 /v1/experimental/demo/retrieve 仍 demo-1；原主应用 /v1/retrieve 仍501。发布 JSON Schema 见 request.schema.json 与 response.schema.json，两仓库导出一致。

请求 Bearer 为独立演示令牌，头部 X-Request-ID 必须等于请求 UUID，X-Tenant-ID=hybrid-demo、X-Actor-Role=operator、X-Patient-Context=false；body.scope_key=hybrid-demo。query 1..4000字符，top_k 1..6，context_budget 100..12000估算单位；域由可信配置列表限制。输入流最多20000字节，响应最多65536字节。无跳转、无自动重试。单进程最多200请求；每请求最多一次Embedding、一次Reranker；provider阶段5秒、服务总计12秒含队列、MetaAgent外层最多15秒。

策略 bm25 / dense / hybrid / hybrid_rerank / hybrid_selection。最后两种真正调用重排；关闭、无候选或失败时 rerank_applied=false。reranker_used 是该策略配置的重排模型名，是否执行必须同时看 model_calls.rerank 与 rerank_applied，不能仅凭该名称宣称运行。

响应含 schema_version、request_id、retrieval_run_id、backend=portfolio_hybrid、strategy、approval=technical_demo_only、clinical_approval=false、scope/domain、corpus_version/sha256、index_sha256。来源含 source_id、chunk_id、doc_ref、document_version、source URI、title/section/section_id、page可空、start/end Unicode字符半开区间、text/text_sha256、score。文本为完整来源片段；本轮纯文本 page=null，不冒称PDF页。HTTPS来源与正整数页可由新协议承载，真实PDF索引尚未验证。score 只用于对应阶段排序，不是概率、准确率或临床置信度。

candidate_count 是融合后去重池数量；post_rerank_count 是预算前数量；selected_count 是最终片段数量。context_tokens_estimated 是完整片段 JSON 的 UTF-8 字节数之和，保守估算、不是执行模型收费 tokenizer。top_k 正常选择不算预算故障；因预算放不下完整片段时 budget_truncated=true，并显式 partial/overflow。

stages 记录 Dense、BM25、RRF、rerank、selection 的 ID与排序分数、当前query向量SHA、模型/维度、provider request_id/usage（可得时）；无Gold、无历史缓存。响应 HMAC-SHA256 签名覆盖除signature以外的 canonical JSON（UTF-8、key排序、无额外空格）。MetaAgent 先验签再验证 request/scope/domain/strategy/pins、稳定ID、引用/版本、区间/hash、预算/计数及大小。

| 条件 | HTTP/检索状态 | MetaAgent |
|---|---|---|
| 正常完整命中 | 200 success | succeeded，仍不代表语义已审核 |
| Dense 或 BM25 故障，另一路有证据 | 200 partial / 显式 degradation | partial，无静默本地替代 |
| Reranker 故障 | 200 partial；保留RRF；rerank_applied=false | partial |
| 两路无命中 | 200 unavailable / no_evidence | no_knowledge_evidence，不编来源 |
| 完整片段放不下 | 200 partial 或 unavailable/context_overflow | partial 或 unavailable；Toolkit还能进一步压缩 |
| token / tenant / patient / domain 越界 | 401/403 | unauthorized/scope/domain denied |
| 请求版本或pin不符 | 409 | rag_version_unavailable |
| 文件/索引漂移、总超时、调用额度耗尽 | 503 | unavailable，停止沿用旧索引 |
| 签名、响应来源/hash/位置不一致 | client拒收 | rag_response_invalid，当前有效调用完整性失败则run failed |
| 有效引用缺失/失效 | final审计拒绝发布 | knowledge_citation_invalid / failed |

示例 request.example.json 可直接检查字段，Authorization由启动器安全提供。response.example.json 来自本轮真实HTTP的脱敏响应，signature已移除；不可把该公开样例当作可重放的签名响应。
