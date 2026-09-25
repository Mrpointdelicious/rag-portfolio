# RAG Portfolio

面向简历和技术面试展示的 RAG 工程项目。目标是构建一套可复现的知识库问答系统，展示文档治理、混合检索、证据引用、可靠发布与评测能力。

本仓库从 MetaKnowledgeBase 的 M1 baseline 独立创建，拥有新的 Git 历史、Python 包名、配置前缀和开发环境。已验证的基础包含 17 张业务表、迁移、鉴权、健康检查、知识库注册和持久任务。本轮新增 [Document → Version → Approved → Chunk 服务层](docs/document-flow.md)，仅支持 Markdown/TXT；代码已准备，尚未运行测试或接入 HTTP。`POST /v1/retrieve` 仍返回 501，完整 RAG 问答按 [开发路线](docs/roadmap.md) 实现。

## 当前能力

| 已有基础 | 后续作品功能 |
|---|---|
| FastAPI + Pydantic 接口与配置 | 文档上传、解析、版本与审核 |
| PostgreSQL + SQLAlchemy + Alembic，17 表 | 通用知识库分类与用户受众 |
| 读取/管理服务令牌、tenant/scope 隔离 | dense/sparse 混合召回、RRF、重排 |
| 持久任务、幂等、重试、租约回写校验 | 有证据的回答、引用定位、无证据拒答 |
| Qdrant 连接与依赖就绪检查 | 完整索引发布、撤销和回滚 |
| Docker、隔离数据库测试、smoke | 评测集、消融实验、演示界面 |

初版数据库保留了来源项目的医疗和场景字段，例如 `rehab_kb`、`hospital_id` 和 `patient/doctor`。它们目前用于兼容已验证的迁移与测试；第一阶段通过新迁移完成通用化。场景功能属于继承模型，不是本作品的开发主线。

## 启动

需要 Python 3.12、uv 和 Docker。在项目根目录执行：

```powershell
uv sync --locked
uv run python tools/bootstrap.py
docker compose --profile app up -d --build --wait
uv run python tools/smoke.py
```

打开 [API 文档](http://127.0.0.1:5052/docs)。`bootstrap.py` 为本项目生成随机凭据到 Git 忽略的 `.env`，已有文件会保留。使用其中的 `RAG_SERVICE_TOKEN` 或 `RAG_ADMIN_TOKEN` 认证。

本机开发模式：

```powershell
docker compose up -d --wait postgres qdrant
uv run alembic upgrade head
uv run rag-api --reload
# 另一个终端
uv run rag-worker
```

本机 API 与容器 API 共用本项目的 5052 端口，二选一运行。若已启动全容器模式，先执行 `docker compose --profile app stop api worker`，再运行本机入口。

| 配置 | 本仓库默认值 |
|---|---|
| Python 包 / 命令 | `rag_portfolio` / `rag-api`、`rag-worker` |
| 环境变量前缀 | `RAG_` |
| Compose 项目 / 镜像 | `rag-portfolio` / `rag-portfolio:local` |
| 数据库 / 数据库用户 | `rag_portfolio` / `rag_portfolio` |
| API / PostgreSQL / Qdrant 端口 | 5052 / 5545 / 6534，仅监听本机 |

数据卷由 `rag-portfolio` Compose 项目独立管理。停止使用 `docker compose --profile app down`，保留数据卷。

## 检查与测试

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
uv run --locked python tools/test.py -q
uv build --no-sources
```

普通 pytest 不连接数据库，数据库集成测试会跳过。`tools/test.py` 在本项目的本机 PostgreSQL 中创建唯一临时测试库，依次执行迁移升级、降级、再次升级、schema 差异检查和完整测试，最后只删除本次测试库。

## 项目结构

```text
src/rag_portfolio/
  api/                 # HTTP、鉴权、依赖
  db/models/           # 内容、发布、场景、任务和审计模型
  services/jobs.py      # 持久任务与租约
  adapters/vector.py   # Qdrant 连接
  main.py, config.py, contracts.py
  serve.py, worker.py
migrations/            # Alembic 迁移
tests/                 # 接口、配置、约束和并发测试
tools/                 # 环境初始化、隔离测试、smoke、文档导入
docs/                  # 架构、开发路线、来源及验证记录
```

[架构说明](docs/architecture.md) · [开发路线](docs/roadmap.md) · [来源与独立化说明](docs/origin.md) · [本仓库验证记录](docs/verification.md) · [文档导入工具](docs/importing-documents.md)
