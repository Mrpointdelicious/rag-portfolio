# 来源与独立化

创建日期：2026-09-25。

| 项目 | 记录 |
|---|---|
| 来源项目 | MetaKnowledgeBase |
| 来源标签 | `m1-baseline-2026-09-25` |
| 来源提交 | `eb25fd3bf2c4cff62f4597b13815611962707dd8` |
| 创建方式 | 导出该标签的已跟踪源码，建立新 Git 仓库 |
| 新仓库 | `rag-portfolio`，独立 `main` 分支与提交历史 |

继承内容包括工程结构、17 张 ORM 表、首版迁移、鉴权、任务队列、接口契约、测试、Dockerfile 与辅助脚本。应用包和导入统一改为 `rag_portfolio`，命令改为 `rag-api` / `rag-worker`，环境前缀改为 `RAG_`。第三方依赖版本保持与来源 baseline 一致；初版迁移定义保持一致。

新项目使用独立数据库 `rag_portfolio`、Docker 项目 `rag-portfolio`、API 5052 / PostgreSQL 5545 / Qdrant 6534。凭据通过本项目 bootstrap 重新生成。

导出不包含来源 `.git`、`.env`、虚拟环境、模型文件、数据库卷、附件或 `.local` 工作产物。原业务 README 和医院/内部系统规划文档改写为本项目的架构和作品路线，来源项目保持原样。

当前没有配置远程仓库。此仓库是本地独立项目，尚未发布到 GitHub 或其他托管平台。
