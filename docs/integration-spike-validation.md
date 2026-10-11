# 独立只读演示接口验收 — 2026-10-11

本次从 `50abf26` 增加默认关闭的 `/v1/experimental/demo/retrieve`。实时复用 LocalBM25Retriever，从显式哈希钉住的自建CC0原文构建内存索引；没有读取医学PDF、`.local/`、Gold或历史检索报告。正式 `/v1/retrieve` 仍为501，P1治理与正式发布语义保留，未假称P2-E完成。

实现/契约见 [设计记录](integration-spike-design.md)、[请求Schema](integration-contract/request.schema.json)、[响应Schema](integration-contract/response.schema.json)。原文在 `fixtures/integration_demo/corpus.json`，clinical_approval=false，仅technical_demo_only；专用演示token固定tenant/scope，拒绝patient、clinician、患者上下文与production。source/page/section/span由底层原文精确映射，无PDF页码推断；score仅排序分数。

实际结果：本仓库193项测试通过、33项数据库/基础设施测试跳过，新增24项隔离ASGI/单元边界用例。见 [原始测试输出](acceptance/integration-2026-10-11/unit-tests.txt)。ruff检查/格式检查、独立Compose配置解析通过；Docker引擎未运行，容器构建/运行未验证。

与MetaAgent的真实联调：12轮真实模型/TCP调用中10项满足全部验收期望，E02/E09部分失败。E02取得两文档六维度片段并正确引用，但后续空域查询使既有MetaAgent状态聚合为unavailable；E02/E09还出现未被原片段明示的指标含义解释。20次引用全部来源有效不代表全部语义支持，更不代表临床正确率。7项真实服务探针及额外1项日志关联证明通过。Mock/单元结果与真实联调分开统计。

本仓库保存一项真实响应及对应日志：[响应](acceptance/integration-2026-10-11/service-trace-proof.json)、[日志](acceptance/integration-2026-10-11/service-trace-proof.log)，request `3604d73f-6213-4f2a-824c-1e4b215659ea` / run `30de4fee-ec4c-4939-bd10-4d98170d672b`。日志只记录请求/检索ID、状态、命中数和耗时，不打印正文或令牌。

源码包/wheel构建通过；归档文件名检查发现原outputs会被收入sdist，已增加显式排除并重建，未打开outputs正文或上传构建产物，原文件不变。

完整模型输入、工具记录、Evidence、回答、E01–E10报告、复现和求职事实在配套MetaAgent仓库的 `docs/remote-rag-acceptance.md`、`docs/remote-rag-runbook.md`、`docs/remote-rag-career-evidence.md` 和 `docs/acceptance/integration-2026-10-11/`。MetaAgent分支 `codex/remote-rag-integration-spike`；两仓库均未提交、推送或部署，原有outputs和.env不变。

单独启动：设置全新的 `RAG_DEMO_TOKEN` 和 `RAG_DEMO_CORPUS_SHA256`（来自审核后的 corpus.sha256），执行 `.venv\Scripts\python.exe tools\demo_server.py --port 18092`。该入口忽略.env，不操作正式DB。Docker示例为独立 `compose.integration.yaml`，无数据库服务/卷，不合并现有部署。默认 `RAG_DEMO_RETRIEVAL_ENABLED=false`；停止独立服务或关闭开关即可回滚。正式患者检索、在线Dense/RRF/Reranker、临床审批与生产发布不在本次验收范围。
