# Document → Version → Approved → Chunk

状态：服务层代码已准备；按本轮要求未运行测试用例、数据库迁移或容器更新。以下是调用约定，不是已验证的运行结果。

## 服务入口

| 文件 | 入口 | 行为 |
|---|---|---|
| `services/documents.py` | `DocumentService.create_document` | 按 KB、source instance、external key 注册资料；返回 `(document, created)` |
| 同上 | `create_version` | 创建 draft；正文及受治理元数据与最新合格版本相同则复用；`force_new=True` 强制新 revision |
| 同上 | `update_draft` | 完整替换草稿，校验 `expected_row_version`；pending 以后禁止原地修改 |
| 同上 | `submit_for_review` | 检查受众和 hash，变为 pending 并冻结，写审计 |
| `services/reviews.py` | `ReviewService.approve_version` | 校验 row_version 和审核时的两项 hash，记录审核人，批准并生成分块 |
| 同上 | `reject_version` | 标记 rejected，保留原因和审核人；再次编辑需创建新 revision |
| 同上 | `chunk_approved_version` | 仅处理未撤销、未过期的 approved 版本；相同配置重跑复用已有分块 |
| `services/chunking.py` | `parse_document`、`chunk_text` | UTF-8 Markdown/TXT 正规化与确定性切分，不访问数据库或文件系统 |

正常流程：`Document → draft Version → pending → approved + chunks`。审核与分块在同一个调用者事务内提交，审核通过并不等于在线发布。

## 调用方式

服务接收 `AsyncSession` 和 `ActorContext`，仅 flush，不自行 commit。所有写操作放入 `database.sessions.begin()`；异常必须触发事务回滚，不能捕获服务异常后继续提交该事务。

准备一份资料并提交审核：

```python
from datetime import UTC, datetime
from uuid import UUID, uuid4

from rag_portfolio.db.session import Database
from rag_portfolio.services.documents import ActorContext, DocumentService, VersionInput


async def prepare_document(database: Database, kb_id: UUID, tenant_id: str, editor_id: str):
    async with database.sessions.begin() as session:
        service = DocumentService(session, ActorContext(tenant_id, editor_id, uuid4()))
        document, _ = await service.create_document(
            kb_id,
            title="项目说明",
            source_instance_id="portfolio-demo",
            external_key="project-guide",
        )
        version, _ = await service.create_version(
            document.id,
            VersionInput(
                filename="project-guide.md",
                content="# 项目说明\n\n这是准备人工审阅的示例内容。\n",
                source_locator="demo/project-guide.md",
                effective_at=datetime(2026, 9, 25, tzinfo=UTC),
                audiences=("internal",),
                metadata={"topic": "project-guide"},
            ),
        )
        if version.review_status == "draft":
            version = await service.submit_for_review(
                version.id, expected_row_version=version.row_version
            )
        return version.id, version.row_version, version.content_hash, version.metadata_hash
```

人工核对该版本全文与元数据后，在另一个事务中批准。`reviewed_*` 参数必须对应审核者实际查看的版本，不能为绕过冲突自动替换成最新值：

```python
from rag_portfolio.services.chunking import ChunkingConfig
from rag_portfolio.services.reviews import ReviewService


async def approve_reviewed_version(
    database, tenant_id, reviewer_id, version_id, reviewed_row_version,
    reviewed_content_hash, reviewed_metadata_hash,
):
    async with database.sessions.begin() as session:
        service = ReviewService(session, ActorContext(tenant_id, reviewer_id, uuid4()))
        return await service.approve_version(
            version_id,
            expected_row_version=reviewed_row_version,
            expected_content_hash=reviewed_content_hash,
            expected_metadata_hash=reviewed_metadata_hash,
            reason="已核对正文、出处和适用范围",
            chunking=ChunkingConfig(max_chars=800, overlap_chars=80),
        )
```

调用者先完成身份认证和管理/审核授权，再构造 `ActorContext`。本轮没有接入新的 HTTP 接口或真实用户身份系统；服务参数中的 actor 不能直接取自不可信请求头/正文。

## 内容与一致性约定

- 只支持 `.md`、`.markdown`、`.txt`；输入为字符串或 UTF-8 字节，允许 BOM，最多 2 MiB。调用者负责读取文件后传入内容；服务不访问用户提供的文件路径。
- 正规化统一换行，保留 Markdown 缩进和行尾空格。`effective_at` 必填且带时区，重复导入使用相同时间值，避免当前时间改变元数据 hash。
- 版本元数据保存标题、文件名、格式、来源及自定义属性快照；元数据 hash 同时覆盖受众和有效期。标题/身份不一致的文档注册返回冲突。
- 沿用 `0001` 模型和受众枚举，通用作品可使用 `internal`；本轮没有修改知识库分类和场景表。
- 创建 revision 时锁定文档；修改、提交、审核按文档 → 版本顺序加锁，并校验 row_version。所有查询绑定 tenant。
- 内容冻结由服务写入路径保证，未增加数据库触发器；其他代码不应直接修改已提交的版本行。
- 批准要求 pending、非空受众和未过期；拒绝后保留原版本，新版本重新审核。批准失败需回滚，禁止留下无对应分块的半次操作。
- chunk ID 由 version ID、chunker version、ordinal 和正文 hash 确定。相同配置重跑不删除或覆盖已有分块，发现已有集合不一致则报冲突。
- Markdown 识别 ATX 标题，忽略代码围栏内的伪标题；TXT 不解析标题。按章节内段落/换行优先切分，长段落用字符窗口兜底，允许窗口重叠，最多 4096 块。
- 这是简单分块器：大表格和代码块可能跨块；`token_count` 是估计值，不作为模型 token 上限保证。后续接 embedding 时再使用真实 tokenizer。

`DocumentServiceError` 提供 `code` 和建议的 `status_code`：对象不存在 404，输入无效 422，行版本/审核 hash/冻结状态/分块集合冲突 409。HTTP 映射留待接口接入。
