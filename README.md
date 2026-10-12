# RAG Portfolio

新增[公开医学 Corpus v1](docs/public-medical-corpus-v1-design.md)：保留旧八篇，收集六篇指定论文，形成14篇本地合并库与7篇经审 prose 公开库；50题独立 Gold、六路真实检索及 MetaAgent 隔离回答已运行。[EVAL](docs/public-medical-corpus-v1-eval.md)保留漏检、原因传递和人工复核待办，未获得临床批准。复现入口见设计文档，实际审查包见 [acceptance.json](artifacts/public-medical-corpus-v1/acceptance.json)。

新增 [MetaAgent Integration Spike](docs/integration-spike-validation.md)：默认关闭的隔离只读演示检索，已完成真实模型→RAG→Evidence→引用回答验证；保留部分失败、审批边界和未验证项。正式 `/v1/retrieve` 仍为501，不代表P2-E或医学生产发布完成。

面向简历和技术面试展示的 RAG 工程项目。目标是构建一套可复现的知识库问答系统，展示文档治理、混合检索、证据引用、可靠发布与评测能力。

本仓库从 MetaKnowledgeBase 的 M1 baseline 独立创建，拥有新的 Git 历史、Python 包名、配置前缀和开发环境。基础工程、[最小内容治理闭环](docs/document-flow.md)与[单文档 Dense Retrieval baseline](docs/dense-retrieval-baseline.md)已完成并验证。[Multi-document Dense Evaluation v1](docs/multi-document-dense-eval-v1.md)已完成两次真实运行：固定 5 文档、43 chunks、29 queries，其中 25 个计分，Recall@1=0.76、Recall@5=1.00、MRR@5=0.863333。`POST /v1/retrieve` 仍返回 501，完整 RAG 问答按 [开发路线](docs/roadmap.md) 实现。

[Retrieval Eval v2](artifacts/eval/multidoc_eval_v2/README.md) 在原始真实 Dense Top-5 上区分 acceptable/preferred evidence，完成 Metadata 和本地 BM25/RRF 离线消融。修正 Gold 后 Dense Recall@1=0.96、MRR@5=0.973333；Metadata 无计分收益，Hybrid 把 P02 从第 3 提至第 2，MRR@5=0.98。来源冲突双方均进入 Top-2；no-answer 分数有重叠，尚无生产阈值。v1 原始结果保留。

新增[独立医学原始 PDF 实验](docs/medical-retrieval-experiment.md)：盘点中全部 180 份 PDF 已分入选/未入选备份，首批 8 份、102 页、736 块，题集为 28 dev / 12 test。医学题集、Gold、索引和结果与上述元宇宙场景实验隔离；Word 文档不进入语料。真实 Dense/BM25/RRF 已运行，[测试结果](artifacts/eval/medical_rehab_v1/README.md)中 Hybrid Hit@5=0.60、完整覆盖@5=0.40（10 道计分题），多篇证据仍不完整，保持离线实验状态。

[医学第二轮](eval/medical_rehab_v2/README.md)已在同一批 PDF 上修订问题与 Gold，并真实比较来源路由、去重和 qwen3.7-text-rerank。[第二轮结果](artifacts/eval/medical_rehab_v2/README.md)：固定 v2 Gold 下，开发完整覆盖从 Hybrid 13/31 提至选定组合 21/31，新保留题从 8/10 提至 10/10；复杂多篇开发题仍仅完整覆盖 2/6。新旧题集口径不同，分数不可跨版本直接比较。[患者实测数据核查](docs/patient-measurement-data-audit.md)未找到可直接使用的可靠测量 Gold，当前数据库未在线核验。

[医学第三轮](eval/medical_rehab_v3/README.md)已实现保留问题主题、子问题召回、按维度选证据和相邻上下文，并冻结 20 道新保留题。[第三轮结果](artifacts/eval/medical_rehab_v3/README.md)：统一 v3 Gold 与 4000 参考 token 预算下，普通重排与新方案的开发完整覆盖分别为 33/41、38/41，保留题为 12/16、15/16。新方案保留题 Top-5 完整覆盖 16/16，但预算内 15/16 与旧版组合持平；上下文扩展没有保留题增益，下一步优先改进证据预算分配。

| 阶段 | 状态 |
|---|---|
| P0 基础工程 | 完成 |
| P1 最小内容治理闭环 | 完成：Markdown/TXT 导入、版本、人工审核、批准后分块 |
| P2-A Dense Retrieval baseline | 完成：10 chunks、6 queries，历史实验保持不变 |
| P2-B Multi-document Dense Evaluation | 完成：43 points 两次运行数量与 Top-5 排名一致；保留失败案例 |
| P2-C Sparse + RRF | 完成 eval-only 对照：固定 BM25、RRF、v2 Gold；生产接入待评估 |
| P2-D Reranker | SKIPPED：provider unavailable；已有 RerankerPort |
| P2-E Release + retrieve API | 未开始 |
| M1 医学 PDF 独立检索实验 | 离线实验完成：备份、原文页码、40 题技术 Gold、真实四路对照；临床审批与回答未完成 |
| M2 医学检索修订与真实重排 | 离线第二轮完成：40 开发题、12 新保留题、7 路对照；独立临床审核和患者实测验证未完成 |
| M3 医学问题拆解与证据预算 | 离线第三轮完成：52 开发题、20 新保留题、七组对照、输入边界检查；保留预算打包失败与排序取舍 |

## 当前能力

| 已有基础 | 后续作品功能 |
|---|---|
| FastAPI + Pydantic 接口与配置 | HTTP 文档上传与更多格式解析 |
| PostgreSQL + SQLAlchemy + Alembic，17 表；版本与审核 | 通用知识库模型迁移 |
| 读取/管理服务令牌、tenant/scope 隔离 | 将经过评测的检索方案接入生产 API |
| 持久任务、幂等、重试、租约回写校验 | 有证据的回答、引用定位、无证据拒答 |
| DashScope Dense → Qdrant → PostgreSQL hydrate | 完整索引发布、撤销和回滚 |
| Docker、隔离数据库测试、smoke、Gold/Metadata/Hybrid 消融 | 真实 reranker、独立 answerability 验证集、演示界面 |

初版数据库保留了来源项目的医疗和场景字段，例如 `rehab_kb`、`hospital_id` 和 `patient/doctor`。它们用于兼容已验证的迁移与测试；通用模型迁移仍待后续实施。元宇宙场景正文保存在被 Git 忽略的 `data/`，医学原始 PDF 保存在 `doc/selected/` 与 `doc/unselected/`；快照和含 evidence 的报告保存在 `.local/`，不随公开代码发布。

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
  adapters/            # DashScope embedding、Qdrant
  evaluation/          # 固定 corpus、Gold、指标、失败报告
  main.py, config.py, contracts.py
  serve.py, worker.py
migrations/            # Alembic 迁移
tests/                 # 接口、配置、约束和并发测试
tools/                 # 环境初始化、隔离测试、smoke、导入、冻结 corpus、评测
eval/                  # 可公开的固定 query 与 Gold 引用，不含完整业务正文
doc/                   # selected/unselected 原始 PDF 备份，正文与来源清单不进入 Git
docs/                  # 架构、开发路线、来源及验证记录
```

[架构说明](docs/architecture.md) · [开发路线](docs/roadmap.md) · [来源与独立化说明](docs/origin.md) · [本仓库验证记录](docs/verification.md) · [文档导入工具](docs/importing-documents.md)


## 隔离只读 Hybrid 服务

新增独立 `POST /v1/experimental/hybrid/retrieve`（retrieval-v1），实时 query Embedding + cosine Dense、BM25/RRF、真实 Qwen 重排、整段来源预算打包。静态8份CC0文档/15chunks，Gold与运行时分离；默认关闭，非生产/无患者，双pin与签名校验。旧demo和正式501接口兼容。见[验收结论](docs/integration-service/00-executive-summary.md)、[运行方法](docs/integration-service/05-runtime-and-deployment.md)、[五个 Review 问题](docs/integration-service/06-review-guide.md)。答案语义仍为PARTIAL。

## 公开医学语料与下一轮方向

[公开医学Corpus v1](docs/public-medical-corpus-v1-design.md)已完成7篇公开正文的实时Hybrid/重排及MetaAgent隔离联调，[评测结果](docs/public-medical-corpus-v1-eval.md)保留全部限制。首轮12道未完整覆盖题已登记为[固定回归与待改进集合](docs/public-medical-corpus-v1-review-questions.md)，本阶段暂缓定点修复。[后续方案](docs/public-medical-next-iteration.md)中的完整知识库服务化与业务评测已按下述候选基线实施。

[合并知识库与业务基线v1](docs/medical-business-v1-results.md)已完成14篇/713块的真实Hybrid候选、版本撤销/回滚、40条件/45轮MetaAgent基线及原50题固定回归。新保留集15轮有证据任务中，10轮必要锚点完整进入最终模型上下文；这不是答案准确率。完整业务验收PARTIAL，旧12题继续暂缓修复。[运行方法](docs/medical-business-v1-runbook.md)。

[v2方案](docs/medical-business-v2-plan.md)已完成失败原因保真、过期证据有限恢复、可信发布能力及独立运营试点。[实施结果](docs/medical-business-v2-results.md)：工程检查通过，业务验收PARTIAL，C不晋级；旧12题继续保留回归。真实患者尚未接入，[生产接入评估](docs/real-patient-production-design-assessment.md)建议下一轮先做主体授权、模型出口控制与合成患者预发验证。

[合成患者预发v1](docs/synthetic-preproduction-v1-results.md)已实现专用授权/出口/撤销契约原型：冻结60项工程条件通过，另有真实Hybrid/Reranker/生成链路检查。原生Agent与既有患者后端尚未接通这些新策略，真实患者和生产仍不接入；此前业务问题与待审集合继续保留。

[原生单Agent合成接入v2](docs/synthetic-native-v2-results.md)把策略接入原生工具循环、缓存、模型与发布检查，验证IReGo/IReTour响应映射；专用入口不自动挂载到默认应用。真实患者和生产仍为NOT_READY，原业务待改进集合继续保留。

[共享授权与.NET合成联调v3](docs/shared-authority-v3-results.md)完成实际.NET控制器主体契约、共享PostgreSQL授权、原生患者运行的进程恢复及物理清理验证，41项固定条件通过。真实登录、租户/跨库映射、实际业务Repository和独立临床审核仍待完成，生产接入继续NOT_READY。

[project数据库授权适配v4](docs/project-identity-v4-results.md)新增实际JWT格式验证、机构角色与近期未结束问诊复核，明确当前project患者ID空间。真实MySQL表结构、原生Agent和.NET控制器的贯穿验证使用合成数据；实际后端逐查询归属约束和持续授权账本仍待完成，真实患者继续NOT_READY。
