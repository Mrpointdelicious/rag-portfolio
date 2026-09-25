# 独立仓库验证记录

日期：2026-09-25。状态：独立仓库基础验收通过。

来源及隔离配置见 [origin.md](origin.md)。本记录仅描述新仓库实际执行的结果，不沿用来源项目的验收结论。

## 基线

- 本地标签：`portfolio-baseline-2026-09-25`。
- Python：本机 3.12.13；镜像 3.12.14；uv 0.11.16。
- 应用：`rag-portfolio 0.1.0`；数据库迁移：`0001`。
- 第三方依赖版本与来源 baseline 一致；锁文件仅更新本项目身份及其规范化表示。
- 初版迁移定义与来源一致，模型业务通用化列入 P1。

## 检查结果

| 检查 | 结果 |
|---|---|
| `uv sync --locked` | 独立虚拟环境安装成功 |
| Ruff 检查与格式 | 全部通过 |
| `uv run --locked python tools/test.py -q` | **24 passed、0 skipped** |
| 隔离数据库迁移 | 升级 → 降级 → 再升级 → `alembic check` 均通过，无模型差异 |
| 测试库清理 | 本次 `rag_test_` 临时库已删除 |
| `uv build --no-sources` | 源码包及从源码包构建的 wheel 成功 |
| wheel 内容 | 包含 `rag_portfolio` 的 4 个 ORM 源文件，无旧 `meta_knowledge` 包 |
| Docker 构建 | `rag-portfolio:local` 构建成功 |
| Compose 启动 | migrate 退出码 0；API 和 PostgreSQL 健康；worker 与 Qdrant 运行 |
| smoke | readiness、鉴权、知识库注册/查询、任务幂等、worker 完成、检索 501 边界均通过 |
| 凭据隔离 | 4 项数据库/向量/服务凭据重新生成，与来源项目不同；`.env` 被 Git 忽略 |
| 环境隔离 | 独立端口、数据库、网络、数据卷与镜像；没有复用来源数据 |
| 来源项目 | 工作区仍干净，提交仍为 `eb25fd3`，5051 API 保持健康 |
| 文档 | 本地链接与代码围栏检查通过 |

pytest 有 2 条依赖弃用警告，来自 TestClient/httpx 和 AnyIO API，未影响测试结果。当前仍是工程基线，检索/生成/评测质量不在本次验收范围。

## 复现

```powershell
uv sync --locked
uv run python tools/bootstrap.py
docker compose up -d --wait postgres qdrant
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python tools/test.py -q
uv build --no-sources
docker compose --profile app up -d --build --wait
uv run --locked python tools/smoke.py
```

本地服务保留运行：[API 文档](http://127.0.0.1:5052/docs)。本次 smoke 在本项目开发库保留了一个兼容原模型的知识库注册与已完成探针任务，未导入实际业务资料。普通停止执行 `docker compose --profile app down`，保留本项目数据卷。
