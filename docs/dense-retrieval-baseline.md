# Dense Retrieval baseline

本实验只验证一条最小链路：approved PostgreSQL DocumentChunk → DashScope document embedding
→ Qdrant → query embedding → Dense Top-5 → PostgreSQL hydrate → 原始 chunk 与单 Gold 指标。

## 固定实验设置

| 项目 | 值 |
| --- | --- |
| Corpus | `hospital-scene-guide` revision 2（岳阳医院元宇宙场景说明） |
| Version ID | `cc725d7d-f8a9-455d-a613-3ccf71705fae` |
| 状态 | `review_status=approved` 且 `revoked_at IS NULL` |
| Format / chunks | Markdown / 10，ordinal 0–9 |
| Chunker | `simple-v1:markdown:c800:o80` |
| Embedding provider | Alibaba DashScope (`alibaba_dashscope`) |
| Base URL | `https://maas.qianwenaiapi.com/api/v1` |
| Model / dimension / output | `qwen3.7-text-embedding` / 1024 / dense |
| Text type | 文档 `document`，查询 `query` |
| Embedding input | `title + "\n\n" + section_path + "\n\n" + text`，各段 strip，忽略空段，不去重标题 |
| Input version | `title-section-text-v1` |
| Embedding revision | `alibaba_dashscope:qwen3.7-text-embedding:d1024:dense:title-section-text-v1` |
| Vector DB / distance | Qdrant / Cosine |
| Collection | `rag_smoke_hospital_scene_v2` |
| Point ID | PostgreSQL `DocumentChunk.id`，无第二套 ID |
| 检索 | Dense Top-5，无阈值、融合或重排 |

## 配置与运行

在本地 `.env` 中配置 `.env.example` 列出的 `RAG_EMBEDDING_*` 值；API Key 必须由本地配置提供。
`.env` 已被 git 忽略，禁止把真实 Key 写入代码、测试、文档或报告。脚本不会打印 Key、请求头或向量。
PostgreSQL 使用 `127.0.0.1:5545/rag_portfolio`，Qdrant 使用 `http://127.0.0.1:6534`。

在仓库根目录执行：

```powershell
uv run python tools/retrieval_smoke.py `
  --version-id cc725d7d-f8a9-455d-a613-3ccf71705fae `
  --recreate

uv run python tools/retrieval_smoke.py `
  --version-id cc725d7d-f8a9-455d-a613-3ccf71705fae
```

`--recreate` 只重建上述固定实验 collection；不带该参数时校验并复用 collection。
每次都重新计算这 10 条 document embedding，通过相同 chunk UUID upsert，完成后必须 `Points: 10`。
脚本不扫描其他版本；显式拒绝其他 version ID、非 revision 2、不同 chunker 或不同语料规模，
避免把这份 ordinal Gold Map 应用于错误语料。

## 实现边界与一致性

- `EmbeddingAdapter` 使用 `httpx.AsyncClient` 调用原生
  `/services/embeddings/text-embedding/text-embedding`，不使用兼容接口或 DashScope SDK。
  timeout 由 config 提供；拒绝空输入，检查 JSON、数量、维度、有限数值与唯一有效的 `text_index`，
  按该索引还原输入顺序。HTTP/网络/结构错误转换为不含响应正文或凭据的 adapter error。
- 已核对本地 `qdrant-client==1.15.1` 的真实签名，检索使用 `query_points`。
  collection 使用 1024 维未命名 COSINE vector。已有 collection 必须维度、距离匹配，
  并且所有 points 的 `embedding_revision` 都一致；缺失 revision 也拒绝写入。
  使用 exact count 比较总点数与匹配点数，不只抽查一个 point。
- payload 仅含 `tenant_id`、`kb_id`、`document_id`、`version_id`、`chunk_id`、`ordinal`、
  `section_path`、`chunker_version`、`content_hash`、`embedding_revision`、`embedding_input_version`。
  不存 chunk 正文、完整文档、标题或伪造的 release ID。
- 检索过滤 tenant / KB / document / version / chunker / embedding revision / input version。
  每个 query 返回后新开 PostgreSQL session，按返回 UUID 回表；再次检查版本批准/撤销状态与归属，
  恢复 Qdrant 排名顺序，并校验 payload 与数据库数据一致。打印的标题、章节、正文来自 PostgreSQL。
- 这是单进程 smoke 实验，不是并发索引/发布协议；运行时不要由其他进程改写同一 collection。
  provider/model/input revision 变化需要显式重建实验 collection。逻辑 revision 不代表供应商模型权重快照。

原生响应字段依据：[Alibaba Cloud 同步 embedding API 文档](https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api)。

## Gold 与指标

| Query | Gold ordinal |
| --- | ---: |
| 岳阳医院二楼有哪些诊室？ | 6 |
| 智能步道康复室在几楼？ | 7 |
| 岳阳医院四楼可以做哪些评估？ | 8 |
| 五楼数据中心主要展示什么信息？ | 9 |
| 怎么返回社交岛？ | 2 |
| 岳阳医院一共有几层？ | 4 |

每个查询只有一个 Gold chunk。`Hit@k` 表示 Gold 在前 k 名；`Recall@k` 为 6 个查询的 Hit@k 均值。
`Reciprocal rank` 为 `1 / Gold rank`，未进 Top-5 记为 0。
summary 的 `MRR` 是这 6 个 reciprocal rank 的均值（检索深度为 5，即 MRR@5）。
未入 Top-5 的 Gold 不推断具体全库排名。

重点观察 ordinal 4：它只有 42 个字符（真实数据库核实），可能是有效的短事实块。
脚本最后额外列出它在六个查询中的 Top-5 排名；用真实输出判断，不修改 query、Gold 或 chunker。

## 验证方式

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python tools/test.py -q
```

单元测试使用 HTTP mock，不会调用阿里云；Qdrant 测试使用已安装 client 自带的内存实现。
`tools/test.py` 是仓库现有的隔离 PostgreSQL 测试入口，自动创建、迁移并清理专用测试数据库；
验证批准/撤销状态、租户边界、chunk 排序和真实 SQL hydration。

本任务不增加 Sparse/BM25、RRF、reranker、LLM、`/v1/retrieve`、KB Release、worker indexing
或 chunker v2。

## 2026-10-02 真实运行结果

已在真实 PostgreSQL、DashScope 和 Qdrant 上依次执行上述两条命令；均退出 0。
两次均读取 10 chunks、得到 10 个 1024 维向量；重建后 `Points: 10`，不重建再次 upsert 后仍为 10。
六个查询的全部 Top-5 chunk ID 顺序在两次运行中相同。

| Query | Gold ordinal | Gold rank（重建 / 复用） | 首次 Gold score |
| --- | ---: | --- | ---: |
| 岳阳医院二楼有哪些诊室？ | 6 | 1 / 1 | 0.781392 |
| 智能步道康复室在几楼？ | 7 | 1 / 1 | 0.703913 |
| 岳阳医院四楼可以做哪些评估？ | 8 | 1 / 1 | 0.839867 |
| 五楼数据中心主要展示什么信息？ | 9 | 1 / 1 | 0.644034 |
| 怎么返回社交岛？ | 2 | 1 / 1 | 0.647937 |
| 岳阳医院一共有几层？ | 4 | 2 / 2 | 0.781809 |

两次 summary 一致：

```text
Queries: 6
Recall@1: 0.833333
Recall@3: 1.000000
Recall@5: 1.000000
MRR: 0.916667
```

“岳阳医院一共有几层？”的首次 Top-2：

```text
[1] score=0.833948 chunk_id=43717f17-d92a-5d01-b3cb-a8237bfb002f ordinal=9
[2] score=0.781809 chunk_id=3088b1e7-fa9c-56f5-9306-31bc5bd5d1f4 ordinal=4
```

ordinal 9 的真实正文也包含“岳阳医院场景一共有几层”的问答及“五层空间”的答案。
保持原始单 Gold=4，未把这一条改成命中@1。ordinal 4 在其他五个查询中均未进入 Top-5，
本轮证据支持它作为精准事实块保留，未观察到抢占其他查询 Top-5；样本仅为这 6 个查询。

两次显示到小数点后六位的 cosine score 有轻微变化，最大差值 `0.000206`，排名与指标均未改变；
这里不据此断言供应商返回的 embedding 能逐位复现，也未进一步归因该数值差异。

完整输出保存在本机 `.local/retrieval-smoke/recreate.txt` 与 `.local/retrieval-smoke/reuse.txt`
（被 git 忽略），包含每个 query 的 5 条真实 chunk 全文、ID、score、ordinal、标题和章节。

验证结果：`ruff check .`、`ruff format --check .` 均通过；`pytest -q` 为 76 passed / 24 skipped；
现有隔离 PostgreSQL runner `tools/test.py -q` 为 100 passed，迁移检查通过，临时库已清理。
没有失败项；存在两条现有 Starlette/HTTPX/AnyIO 弃用告警。
真实 Qdrant client 另提示本地 HTTP 连接配置了 API Key；实验目标为 `127.0.0.1:6534`，未输出凭据。
