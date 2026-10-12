# Public Medical Corpus v1：实现与复现

日期：2026-10-12。状态：技术实现 DONE；人工许可/Gold/临床评审 PARTIAL；未知权利旧全文的外部模型处理 BLOCKED。

## 固定边界与实际数据

工作分支 `codex/public-medical-corpus-v1`，基于 RAG `5621efe83129fd6c29586ca5daab4d7b175207f4`。MetaAgent 仅作只读参考和隔离进程验证，HEAD 保持 `ee489a40f83b7fdc261c7b59c27f7f88bd7306df`，原有六项未跟踪评测材料保持不变。没有 push、merge、修改已有服务或连接生产数据库。

| 数据集 | 作品 | 段落/页段 | 新 chunks | release | 实际索引 |
|---|---:|---:|---:|---|---|
| medical_combined_v1 | 14 | 318 | 713 | medical_combined_v1-prose-2 | 独立本地 BM25，三个来源/版次查询已运行 |
| medical_public_v1 | 7 | 251 | 323 | medical_public_v1-prose-1 | 实时 Dense、BM25、RRF、真 Reranker |

合并库包括旧八篇与新增六篇。公开库是新增六篇与 D161 的经审查 JATS 正文派生内容；D161 的新 PDF/XML 是同一旧作品的另种资源，不增加作品数。旧实验仍为 8 PDF、102 页、736 chunks，157 个源文件/Gold/排名/报告的哈希保持不变。新增六篇 PDF 共 102 页，候选 14 篇的 PDF 共 204 页；XML 段落数不能当成 PDF 页数。

新 chunk 采用 1100 字符、120 字符重叠与保守句末截断，与旧 chunk 数不可比较。合并库旧 PDF 继续使用 pypdf/PDFium 及既有质量排除规则；未改旧解析结果。D041 作者最初在新清单误录，首页复核后更正为潘钰等；首次合并 release 在本地保留，prose-2 使用新指纹，公开语料、Gold 和 Dense 索引指纹均未变。详见 `release_changes.json`。

完整 pin 位于 `artifacts/public-medical-corpus-v1/manifest.json` 与 `index_manifest.json`。公开 corpus pin 为 `1a7ee6eac727938e5f872520778d12c6417a921a54b240a9883cbc7b3759d0ee`；Dense index pin 为 `b3c83b4d8087b087f41926383c10bed19487aed067fd51599b02ac2f6c1ab8f9`。合并库当前 pin 为 `0253a1c5453eb8ea9b4cc8d02cd49989c7b1f3f5dc956ff3774a1a5a3d33dace`。

## 来源、位置及发布契约

`public_medical.py` 保存作品 DOI、文档/资源版本、原文件 SHA256、parser_version、source/chunk UUID、页/章节、XPath、字符区间、文本 SHA256、许可和 `clinical_approval=false`。UUID 同时绑定 source URI、release 资源版本及 section_id，公开/合并命名空间不同。验证器校验 corpus pin、作品唯一性、原句区间、段落哈希和权利门控。

XML 使用 `normalized_jats_paragraph`：offset 属于规范化段落，`page=null`。PDF 使用 `normalized_pdf_page`：page 为实际物理页。49 个首选 Gold 锚点中 39 个通过完整规范化原句匹配找到 PDF 页，另 10 个明确未对齐；即使有辅助对齐，XML 的 page 仍保持 null。对应关系见 `pdf_xml_alignment.json`。

公开仅包含 abstract/body 段落；排除图、表、补充文件、参考文献、致谢、附录及 `sub-article` 同行评审往来。原始 PDF/XML、向量、完整模型消息与未知许可旧全文位于 `.local/public_medical_corpus_v1/`，被 Git、源码包及 Docker context 排除。公开文本保留作者、DOI、CC BY 4.0 链接、资源哈希和规范化/排除说明；这项技术许可审核不赋予患者端临床批准。

## 检索及 MetaAgent 接口

`public_medical_retrieval.py` 的运行时不能读取 Gold。文档向量实际调用已配置 embedding 服务生成，323 条向量由 33 次批请求建立不可覆盖索引。检索每个新 query 实际调用 embedding 并作精确 cosine 扫描，BM25 使用同一文本，RRF k=60，候选深度各 20，重排深度至多 30，再调用 `qwen3.7-text-rerank`。不依赖 Qdrant，也未把纯 BM25 宣称为 Hybrid。

六路消融共享同一次实时 query embedding 和重排响应，比较的主要变量为排序、来源覆盖和相邻文本扩展，不把共享响应算作六次模型调用。top_k=5；均以 cl100k_base 对序列化证据计数，预算上限 4000。coverage 只根据用户问题中的来源标识与词项取舍；context-expanded 只追加同段相邻原始 chunk。未使用历史 Top-5、静态答案或 Gold 路由。

`serve_public_medical.py` 是显式启动、绑定 loopback 的测试入口，使用独立临时 token、operator scope、公开库/索引 pin、HMAC 签名及既有 retrieval-v1 契约。MetaAgent 的 KnowledgeAdapter → TaskResult → Evidence → FactBuilder → 单 Agent 路径实际运行，不修改其 Runtime。该服务的预算沿用契约的 UTF-8 字节上界（此次 12000）；它与离线 EVAL 的 4000 cl100k 预算分别报告，不作等价分数比较。默认服务和生产 API 未接入新语料。

## 复现

在 RAG 仓库、已安装 `medical` 依赖且已有合法模型配置的环境执行。密钥由程序读取，不能放入命令、报告或公开材料。

```powershell
uv sync --extra medical
.venv\Scripts\python.exe -X utf8 tools/collect_public_medical.py --help
.venv\Scripts\python.exe -X utf8 tools/collect_public_medical.py --include-d161
.venv\Scripts\python.exe -X utf8 tools/prepare_public_medical.py
.venv\Scripts\python.exe -X utf8 tools/freeze_public_medical_gold.py
.venv\Scripts\python.exe -X utf8 tools/audit_public_medical.py
.venv\Scripts\python.exe -X utf8 tools/eval_public_medical.py index
.venv\Scripts\python.exe -X utf8 tools/eval_public_medical.py eval --output .local/public_medical_corpus_v1/runs/independent-reproduction-1
```

收集器最多 60 个计划步骤、每资源 20 MB、30 秒超时，保留失败及下载哈希。已存在索引拒绝覆盖；复现本次索引时使用保留的本地文件。重新构建须以 `--index-path` 选择新索引文件（文件名即 index_version），以 `--index-manifest` 选择新清单，EVAL 使用同一 `--index-path`；模型响应不保证逐字相同。旧八篇是本地前提，缺失时 `prepare`/旧审计会失败，不能凭公开 checkout 宣称合并库已重建。公开 checkout 自带经审查 `public-prose.json` 与封存 Gold，可直接构建公开索引；向量没有公开，首次重建也须使用新 index-manifest，避免与发布的既有指纹混写。

```powershell
.venv\Scripts\python.exe -X utf8 tools/verify_hybrid_postgres.py --output .local/public_medical_corpus_v1/verification/independent-db-1
.venv\Scripts\ruff.exe check src tools tests
.venv\Scripts\ruff.exe format --check src tools tests
.venv\Scripts\python.exe -X utf8 tools/verify_public_medical_package.py
D:\Project\metaAgent\.venv\Scripts\python.exe -X utf8 tools/public_medical_agent.py --meta-repo D:\Project\metaAgent --rag-repo D:\Project\rag-portfolio --output .local/public_medical_corpus_v1/agent/independent-1
```

数据库检查只创建并清理带随机标识的自有测试容器。包检查实际构建 wheel/sdist、构建并导出自有 Docker context 镜像进行内容检查，随后清理；没有把配置解析或 context 镜像当作医学应用容器启动验证。使用当前 live query 重现会产生新的服务费用；本次所有失败、尝试和请求元数据已保留在本地。
