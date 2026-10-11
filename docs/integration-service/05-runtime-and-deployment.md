# 运行、容器与回滚

基线远端/本地：RAG main=e0add4d，MetaAgent专用集成分支=2425eaf；master分叉未合并。MetaAgent既有未提交文档保留；本轮专用分支同名codex/hybrid-retrieval-service。实现提交见provenance.json；验收期间源码指纹逐文件保存。没有修改已有.env、outputs、.local、Dify或生产数据。

先审核fixtures/hybrid_demo的CC0合成文档、manifest和hash。已有index为真实文档Embedding，可只读复用。重建应写新文件，禁止覆盖冻结索引：

```powershell
Set-Location D:\Project\rag-portfolio
uv sync --locked
.venv\Scripts\python.exe tools\build_hybrid_index.py --corpus fixtures/hybrid_demo/corpus.json --corpus-sha256 (Get-Content fixtures/hybrid_demo/corpus.sha256).Trim() --output fixtures/hybrid_demo/index-new.json
# 新文件需重新批准/pin后，显式配置RAG_HYBRID_INDEX_PATH与SHA；模型/维度须匹配。
```

本机真实检索+Agent验收会自行分配loopback端口、生成内存令牌、启动owned服务并在finally停止，只加载授权模型配置：

```powershell
Set-Location D:\Project\rag-portfolio
.venv\Scripts\python.exe tools\eval_hybrid_service.py --output docs/acceptance/my-retrieval-run
Set-Location D:\Project\metaAgent
.venv\Scripts\python.exe tools\hybrid_acceptance.py --rag-repo D:\Project\rag-portfolio --allow-existing-model --output docs/acceptance/my-agent-run
.venv\Scripts\python.exe tools\verify_hybrid_docker.py --rag-repo D:\Project\rag-portfolio --output docs/acceptance/my-docker-run
```

单独服务入口 `python -m rag_portfolio.hybrid_serve --port 18102` 默认关闭；需在安全进程环境中提供RAG_HYBRID_ENABLED=true、TOKEN、CORPUS_PATH/SHA256、INDEX_PATH/SHA256及授权RAG_EMBEDDING_*。不要把密钥写入命令历史/代码或打印完整设置。MetaAgent显式knowledge_backend=remote_hybrid、对应URL/token/tenant/scope/version/双pin、knowledge_hybrid_domains=[help]；推荐隔离演示使用已冻结business-grounded-v9及business_grounding_enabled=true。local_bm25仍默认，remote_rag保留旧协议兼容。

Docker初次引擎不可用；随后检测29.7.2，实际Dockerfile.hybrid构建两个镜像，Compose项目metaagent-hybrid-acceptance仅使用18102/18103，独立network、只读文件系统、tmpfs、无生产卷。先health再从Agent容器解析rag-hybrid并HTTP200，再真实Agent→DNS/TCP→Embedding/BM25/RRF/rerank→Evidence→回答。final镜像ID：{'rag-portfolio:hybrid-isolated': 'sha256:9cbfe885d04154c0dbcd71c621f25c3a48c98127b5352edbaa4bf8e3bd246764', 'meta-agent:hybrid-isolated': 'sha256:0426ae54edd62717014e064e174811d28b5a4162503834315b5400677a6bba6b'}。真实build/up/DNS/TCP/chat/down日志见MetaAgent docker-final；清理后owned容器为空：''。初次成功docker-run-3也完整保留；前两个仅启动器缺依赖诊断，未运行容器，不算通过。

手动Compose需要通过安全环境提供专用demo/Agent token、Embedding和模型配置，不保存文件；专用启动器会完成。停止仅用本次项目：`docker compose -p metaagent-hybrid-acceptance -f compose.hybrid.yaml --profile hybrid-demo down --remove-orphans`，不删除其他项目、生产卷或数据。单独RAG profile名hybrid-demo，project rag-hybrid-isolated；可用probe profile验证同network DNS/TCP。

普通回归会跳过未配置测试数据库的33+1项；本轮另外新建随机名称/tmpfs PostgreSQL容器、本机随机端口。Postgres16拉取网络超时保留失败日志；改用本地postgres:15-alpine，RAG仅调用既有tools/test.py，在临时rag_test_*数据库执行迁移/降级/升级/pytest后删除；MetaAgent使用同一owned容器内独立metaagent_test。最终RAG252 passed / MetaAgent305 passed，0 skipped；DB与owned容器均清理。原有PG容器未连接。

```powershell
Set-Location D:\Project\rag-portfolio
.venv\Scripts\python.exe tools\verify_hybrid_postgres.py --meta-repo D:\Project\metaAgent --image postgres:15-alpine --output docs/acceptance/my-postgres-run
```

失败注入只改临时副本/受控代理：首次503、TCP超时、签名后篡改、corpus/index追加字节、删除隔离Evidence。pin文件和原.env不改。真实调用有请求上限/超时；费用账单未获取，不估算。本轮使用的模型、Docker、数据依赖已具备；PG16镜像下载仍受阻，本轮仅验证PG15。语义/临床审查限制单独保留。语料/Gold及raw trace通过.gitattributes保留原始字节，防止Windows换行使SHA失效。
