# 合成患者预发 v1：完成的验证与剩余边界

2026-10-12。已完成专用预发原型及工程验证，**真实患者与生产仍不接入**。原生单Agent、既有AI WebAPI和生产镜像保持原状；本轮新策略尚未贯穿这些旧入口。此前C仍不晋级，v2业务PARTIAL和全部待审问题不变。

## 已实现

新增独立的签名主体协议`synthetic-subject-v1`，绑定issuer、audience、tenant、actor、role、patient、session、purpose、授权代次及不超过120秒的有效期。每个audience使用独立自有密钥；患者关系、授权撤销及会话绑定在自有SQLite中持久化。患者服务与知识服务每次通过自有授权服务复核主体；服务Token本身不能获得患者访问权。重新授权不会恢复旧代次Token或旧会话。

患者工具只按已验证主体选取自建记录，采用新版本规范化只读测量契约`synthetic-measurements-v1`。没有复用手机号映射，没有读取现有后端。有效数值、实际零值、占位零、缺单位、缺值、过期/未来/无日期、歧义分别处理；无效项目显示明确状态且不补数值。严格拒绝非数值、非有限数值及未批准字段。

知识服务新增专用`/v1/preproduction/retrieve`，明确标记`patient_context: synthetic`，校验knowledge audience及当前患者关系。只有两个预设医学概念可以构造查询；不通过旧operator/无患者路径代理请求。来源响应绑定作用域、发布代次、语料/index双pin、有效期、正文hash和独立签名。

生成出口只接受固定概念和批准的文献原文。患者身份、记录、自由问题及完整历史没有输入字段；患者记录按结构化投影在本地展示。模型生成文本单独保留供私有审阅，本轮不向客户端发布未审生成陈述。客户端可以查看有效文献原文，文献值始终与患者实测分开。

最终展示、来源查看、快照和SSE重放都复查主体、授权代次、作用域、有效期与来源发布状态；SSE在后续数据事件交付前再次检查。撤销阻止后续交付，不能撤回已经发送的数据。保存的结果另外校验摘要，检测存储内容变化。

## EVAL与实际结果

60个条件及预期HTTP结果在代码实现与运行前冻结，三类各20个。通过四个自有本机HTTP服务执行；患者、来源和生成器为自建受控夹具。最终**60/60通过**，同时检查正向允许任务完成、错误不带事实、患者字段不进入生成输入、自由问题在出口前阻断。该数字是工程契约结果，不能解释为患者问答准确率。

身份类覆盖有效患者/已授权临床人员、签名篡改、过期/未来/超长Token、错误audience/issuer、跨租户、未授权患者、自报身份、角色伪造、用途/会话冲突、撤销/重新授权和换患者后旧会话。事实类覆盖数值、零值、单位、时间、缺失、同名患者隔离、后端异常及持久化。边界类覆盖来源签名/hash/双pin/时效、撤销中途生成、SSE中途撤销、重放/引用阻断、处方任务拒绝以及个人记录和文献分离。

另跑一次真实链路：使用原批准的14篇/713块、原语料/index和模型配置，**1次Embedding、1次重排、1次生成**，8项检查通过。实际供应商JSON请求体已观察，未出现合成患者标识、姓名或记录字段；不记录请求头。原文引用可以解析。原始生成文本与请求体只保存在私有`.local/synthetic_preproduction_v1/live-run-1/private-model-review.json`，语义标签NOT_ASSESSED。真实供应商只接收非敏感概念与已批准原文。

新增24项底层测试通过，包括原生入口不会自动挂载预发路由，以及自有服务地址不能被伪装成外部地址以转送凭据。完整自建临时PostgreSQL测试最终RAG **284通过**、MetaAgent **345通过**，临时库已清理。持久化条件验证从相同SQLite创建新应用后可读取既有记录；不声称已测生产进程故障切换或多副本一致性。

保留失败尝试：首次60条件运行因启动环境缺RAG模块未实际完成HTTP验证；修正导入路径后完整运行通过。真实链路最初缺SQLAlchemy，使用独立临时依赖环境解决，没有修改生产环境。首次完整测试的路由枚举检查不兼容当前FastAPI，已改为实际HTTP 404检查后重跑。冻结预期未修改，没有挑选模型输出补分。

## 复现

从RAG仓库根目录执行，所有服务只绑定自有随机本机端口，完成后关闭，不操作既有容器：

```powershell
D:/Project/metaAgent/.venv/Scripts/python.exe tools/eval_synthetic_preproduction.py --meta-repo D:/Project/metaAgent --output .local/synthetic_preproduction_v1/new-contract-attempt
uv run --project D:/Project/metaAgent --no-sync --with-editable D:/Project/rag-portfolio -- python tools/verify_synthetic_preproduction_live.py --meta-repo D:/Project/metaAgent --registry .local/medical_business_v1/releases/registry.json --output .local/synthetic_preproduction_v1/new-live-attempt
```

每次使用新的输出目录；不覆盖失败记录。模型凭据只由程序加载，不复制到新配置、日志或交付包。完整患者夹具、SQLite和模型原文均留在被忽略的私有目录。实际wheel/sdist交付审计与此前已提交文件的保护检查另附机器记录。

## 下一道门槛

下一步应将这些契约接入**合成数据的原生单Agent主链**，完成真实AI WebAPI响应到规范化患者事实的映射及对照测试；上游真实身份发行、租户/医患关系不能由本轮自建授权簿替代。之后还需把撤销检查贯穿原生Checkpoint、所有SSE/历史入口与患者工具缓存，并迁移到生产数据库/共享授权服务，完成TTL物理删除、网络/TLS、限流和恢复演练。

此后再完成独立来源语义与临床验收，另行批准真实数据影子试点。本轮不能绕过这些门槛。原12题、E09/B07及297轮待审记录继续保留，不因60项工程条件通过而注销。

本轮没有重跑原50题、v1/v2业务问答或E09/B07，也没有调整检索/重排算法及旧提示；这些历史题不计入60项契约分母，不重新计为通过。已有业务结果逐字节保留。专用入口接入原生Agent主链时，需要再次运行这些固定回归。

详细机器记录：[工程汇总](../artifacts/synthetic-preproduction-v1/summary.json)、[交付与保护审计](../artifacts/synthetic-preproduction-v1/delivery-audit.json)。上轮依据：[真实患者生产设计评估](real-patient-production-design-assessment.md)。
