# 导入外部 Markdown/TXT 文档

状态：工具与测试已实现并验证（2026-09-25）。导入链路止步于 pending；审核、分块、embedding 与检索属于后续阶段。

## 工具解决什么问题

RAG 内容治理的第一步是把外部资料稳定、可解释地送入审核队列。`tools/import_documents.py` 把
`外部 Markdown/TXT 文件 → SourceDocument → DocumentVersion → pending` 这条链路做成可重复执行的 CLI：

- 导入前做 manifest、文件、知识库三层预检，`validate` 不写数据库；
- 每篇文档一个独立事务，单篇失败不影响其余文档；
- 相同 manifest 重复导入幂等，不产生重复文档或版本；
- 每轮导入生成 `import_report.json`，机器可读。

## manifest 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `kb_id` | UUID | 目标知识库，必须存在且 tenant 一致 |
| `tenant_id` | string | 知识库所属 tenant |
| `source_instance_id` | string | 本批来源标识，与 `external_key` 共同构成文档身份 |
| `defaults.audiences` | string[] | 默认受众，允许 `patient` / `doctor` / `internal` |
| `defaults.effective_at` | ISO 8601 带时区 | 生效时间，必须固定（见下） |
| `defaults.expires_at` | ISO 8601 带时区 / null | 过期时间，须晚于 effective_at |
| `defaults.metadata` | object | 默认元数据，document 级同名键覆盖 |
| `documents[].external_key` | string | 文档稳定身份键，manifest 内唯一 |
| `documents[].title` / `filename` / `source_locator` | string | 标题 / `documents/` 下的文件名 / 来源定位 |
| document 级 `audiences` / `effective_at` / `expires_at` / `metadata` | | 可选，覆盖 defaults；`expires_at: null` 可显式清空默认值 |

文件固定从 manifest 同目录的 `documents/` 读取；仅支持 `.md` / `.markdown` / `.txt`，UTF-8（允许 BOM），
非空，最大 2 MiB。`parse_document()` 是文件内容校验的唯一实现，工具不复制解析逻辑。

## 命令

```powershell
uv run python tools/import_documents.py validate data/imports/yueyang_scene/manifest.json
uv run python tools/import_documents.py import   data/imports/yueyang_scene/manifest.json
uv run python tools/import_documents.py status   data/imports/yueyang_scene/manifest.json
```

`validate` 只读：检查 manifest 结构、每份文件、知识库存在性与 tenant 匹配，有错误时列出全部错误并返回非 0。
`import` 先执行相同预检；manifest 级或知识库错误整批不开始，文件级错误只影响对应文档。`status` 按
`kb_id + source_instance_id + external_key` 查询每篇 SourceDocument 及其最新 Version，不修改数据库。

## SourceDocument 与 DocumentVersion 的区别

- `SourceDocument` 是来源身份：`(kb_id, source_instance_id, external_key)` 唯一确定一篇，不随内容变化；
- `DocumentVersion` 是一次内容快照：revision 递增，经历 `draft → pending → approved/rejected`，提交后冻结；
- 内容或受治理元数据变化 = 同一 SourceDocument 下新增 Version；身份不变 = 不新建文档。

## external_key 的稳定身份语义

`(kb_id, source_instance_id, external_key)` 三元组是文档身份。external_key 必须长期稳定，改名等于新文档；
同一身份下 title / source_system 不一致会触发 `document_identity_conflict`，工具如实报错，不自动修复或覆盖。

## effective_at 为什么必须固定

`metadata_hash` 覆盖 `audiences + effective_at + expires_at + metadata`。若每次导入自动取当前时间，
metadata_hash 必然变化，重复导入会被判定为“内容变化”而不断产生新 revision，幂等被破坏。
因此 effective_at 必须由 manifest 显式给出（带时区），重复导入保持同值。

## 重复导入为什么是幂等的

- `DocumentService.create_document` 按身份三元组复用现有 SourceDocument；
- `create_version` 在最新版本未撤销且处于 draft/pending/approved、内容与元数据 hash 均相同时复用该版本；
- 已 pending / approved 的版本不重复提交、不降级，工具如实报告 `version: reused` 与当前 review_status。

工具自身不另造去重规则，全部依赖现有 Service 的身份与 hash 语义。

## 为什么 import 只到 pending

- 内容进入 pending 即冻结，不可原地修改；人工审核需要完整上下文与责任归属；
- 自动 approve 会绕过审核环节，与本项目的文档治理目标冲突；
- 分块、embedding、Qdrant 索引在 approve 之后才有意义，提前执行只会产生需要回滚的半成品。

## approve / chunk 属于下一阶段

`ReviewService.approve_version`（pending → approved，同一事务内原子生成分块）与 `reject_version`
属于人工审核阶段，对应链路 `pending → 人工 review → approved → DocumentChunk`。之后才是
release 构建、embedding、Qdrant 索引与检索。导入工具不接触这些代码路径。

## 常见错误及处理方式

| 错误码 | 含义 | 处理方式 |
|---|---|---|
| `manifest_invalid_json` / `manifest_invalid` | manifest 无法解析或根不是对象 | 修正 JSON |
| `kb_id_invalid` / `tenant_id_required` / `source_instance_id_required` | 身份字段缺失或非法 | 补全 manifest |
| `documents_required` / `duplicate_external_key` / `external_key_required` | 文档列表为空、身份键重复或缺失 | 修正 manifest |
| `title_required` / `filename_required` / `source_locator_required` | 文档条目字段缺失 | 补全条目 |
| `invalid_audience` / `audience_required` | 受众值非法或为空 | 仅使用 patient / doctor / internal |
| `timezone_required` / `invalid_datetime` | effective_at / expires_at 不是带时区的 ISO 8601 | 补时区偏移 |
| `invalid_effective_window` | expires_at 不晚于 effective_at | 调整时间 |
| `invalid_metadata` | metadata 不是对象或不可 JSON 序列化 | 修正元数据 |
| `file_not_found` / `path_traversal` / `unsupported_extension` | 文件缺失、路径越界、扩展名不支持 | 检查 `documents/` 目录 |
| `file_too_large` / `invalid_document_content` | 超过 2 MiB、非 UTF-8、空或含 NUL | 修正文件内容 |
| `knowledge_base_not_found` / `knowledge_base_tenant_mismatch` | 目标知识库不存在或 tenant 不符 | 确认 kb_id 与 tenant |
| `document_identity_conflict` | 同一身份下 title/source_system 与库内不一致 | 人工核对，工具不自动覆盖 |
| `row_version_conflict` | 并发修改冲突 | 重跑该文档导入 |
| `internal_error` | 未预期的服务或数据库错误 | 查看 `import_report.json` 中的有限信息并排查 |
