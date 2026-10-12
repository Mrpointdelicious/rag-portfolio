# 共享授权与.NET合成联调 v3

2026-10-12。本轮已完成共享PostgreSQL授权簿、受信任合成登录签发、实际.NET控制器主体校验，以及原生Agent患者策略在PostgreSQL上的进程重启和物理清理验证。工程验收通过；真实患者生产接入仍为 **NOT_READY**，C仍为 **NOT_PROMOTED**，业务验收继续 **PARTIAL**。

## 实现与验证边界

授权服务在独立数据库`synthetic_authority_v3`保存关系代次和会话绑定。`/sessions`从服务器配置的合成登录令牌取得tenant/actor/role，客户端仅选择patient/session；每次签发检查当前关系，operator不能签发患者主体。`/introspect`要求调用服务身份，检查签名、issuer、audience、用途、角色、时效和当前代次。撤销及重新授权都会递增代次，旧令牌和旧会话不能复活。并发更新使用数据库事务锁。

.NET宿主独立位于MetaAgent的`preproduction/patient-tools`。它直接编译链接当前AI_WebApi目录中的`PatientLayerToolsController`、`IReGoToolsController`及解析器接口，使用实际Model/IServices项目和DTO；控制器查询前、返回结果序列化前各查一次共享授权，解析器核对患者和会话。数据服务使用自建合成夹具，没有调用真实数据库。仅开放档案与IReGo单次分析两条路由，其他控制器操作拒绝。生产Program.cs和后端源码均未修改。

这是真实.NET进程和实际控制器的HTTP契约联调，**不等于现有生产镜像已经具备此功能，也不等于真实业务Service/Repository已验收**。宿主用本机.NET 10运行，引用当前net6.0后端契约；现有生产net6.0宿主中的兼容性、部署及真实关系提供器尚待验证。IReTour沿用上轮6项映射回归，本轮没有新增真实.NET IReTour服务联调。

原生入口显式选择`patient_transport=dotnet`，默认仍使用既有合成传输。原生LangGraph工具循环使用PostgreSQL Store/Checkpoint，授权、患者接口、知识服务和Agent分别运行独立操作系统进程；知识服务本轮返回自建合成文献，模型为受控本地模型。它们验证服务契约与原生执行，没有增加供应商请求。上一轮两次真实Hybrid/Reranker/模型联调是历史证据，不能计入本轮分母。

## 验收结果

| 检查 | 本轮结果 | 说明 |
|---|---|---|
| 首批固定工程条件 | 36/36通过 | 在本轮新增功能实现前封存，包含正向完成、越权、失效依赖及重启 |
| 补充故障条件 | 4/4通过 | 在补充故障注入实现/运行前另行封存；并发授权/绑定、分页清理、执行中撤销 |
| 停机恢复清理条件 | 1/1通过 | 另行封存后实现；常规TTL先删除运行记录，仍能物理删除关联会话 |
| 原生v2回归 | 42/42通过 | 含IReTour 6项；默认传输兼容 |
| 独立原型v1回归 | 60/60通过 | 与原生/共享授权条件分开统计 |
| 完整独立PG测试 | RAG 284、MetaAgent 372通过 | Meta新增8项HTTP授权边界测试；自有临时数据库已移除 |
| 实际交付检查 | 4个wheel/sdist、2份Docker上下文通过 | 不含私有证据、语料或既有凭据，检查用镜像/容器已清理 |

完整条件在`eval/shared_authority_v3`，封存SHA保持不变。这是工程条件集，不是独立业务Gold。前两轮36项分别为34/36、36/36；加入4项后第三、第四轮均40/40；补充停机恢复条件后，第五、第六轮均41/41。首轮A30请求漏传引用参数，得到422，修正为有效引用的撤销重放请求；没有把422算作授权通过。A27发现清理遗漏3条运行审计行，已修复清理逻辑。最后审查补充独立会话删除标识，避免常规TTL先删运行记录后丢失清理关联；整个作用域删完后才删除所有封套，保留中断后重试所需关联。全部失败尝试保留，未修改预期来补分。

最终一轮实际停止并重新启动Agent、授权服务与.NET进程后，已完成运行的授权快照仍可读取；撤销后的快照、引用和事件重放全部拒绝；重新授权不能恢复旧令牌/旧会话，必须用新代次、新会话。已有单实例锁拒绝第二个Agent进程。此处验证的是已完成运行读取恢复，未提供执行中断后的患者任务resume接口，也不声称Agent多副本已支持。

执行中撤销测试先确认实际.NET控制器已经进入合成数据Service，再撤销关系；查询返回403，没有交付患者DTO。每个服务实时检查已提交代次，仍不能据此宣称多个服务之间具有原子撤销，或撤回已经交付的内容。

过期故障在自有库中显式注入。最终一轮在常规TTL删除1条运行记录后，物理清理前Store有172行，清理后仅保留1条相邻有效作用域的数据；过期运行封套为0，失去运行行的会话也已删除。Checkpoint 10行、Blob 14行、Write 14行全部降至0。额外151条证据验证分页删除，重复清理成功且保留相邻数据。清理在启动监听前或停止原生worker后由持有独占数据库租约的维护进程执行；不声称已完成在线并发删除、备份/WAL清除或患者授权簿的保留周期治理。同一过期作用域内会保守删除其他运行，需要重新发起请求。

本轮记录4次受控原生模型调用的实际消息，未见姓名、电话、历史标记和0.42个人测量；患者事实由本地结构化结果交付，生成内容保持待审。没有对真实患者外发做授权推定。

## 原业务问题保持

本轮不修改语料、索引、Embedding、重排参数或业务提示，也不针对已打开题补分。原12道未完整覆盖题、E09不稳定、B07缺少独立IReTour历史/分析、上轮新增锚点缺失继续保留。此前297轮/1924句段和125份/1097句段待审集继续为NOT_ASSESSED；后者与上轮已发布SHA核对一致。本轮另保存2份受控合成模型完整生成及完整HTTP结果，状态同为NOT_ASSESSED，不作为医学能力评分。本轮没有重跑真实供应商的整套医学业务题，不能宣布新的语义进步或消除上轮观察到的可能退化。

## 真实患者生产设计再评估

本轮缩小了共享授权与持久化验证的工程差距。下一步应先形成可部署的**受信任登录—医患关系—租户/患者映射**方案，并把校验接入确定版本的真实后端，随后安排独立临床/来源审阅。暂不扩大自由病历问答。

| 尚缺环节 | 当前源码依据与影响 | 下轮验收要求 |
|---|---|---|
| 真实登录和关系提供器 | 当前后端Program及患者控制器未发现可用于本链路的JWT/SSO发行验证；v3仅合成登录令牌/人工夹具关系 | 明确上游issuer/JWKS或服务端会话适配器；actor、role、tenant由上游验证取得；患者本人和医生关系来自权威系统 |
| 租户与跨库患者映射 | TrustedPatientContext只有PatientId/SubjectRef等字段；PatientLayerRepository按project患者ID查询，没有租户过滤；既有identity mapping依赖服务令牌和手机号/ID | 由受信任映射产生project/robotdb/IReTour不同ID；数据库连接或查询按租户限制；禁止把手机号匹配或客户端ID直接当作访问授权 |
| 后端部署基线及所有患者路径 | 本次使用当前工作目录快照，包含未提交三层实现；生产Program未挂载新校验 | 先确认可复现提交/制品，再在独立合成库运行实际Service/Repository；封住旧工具、报表、下载和缓存路径的绕过 |
| 独立来源语义和临床审核 | 原完整待审队列尚未独立审阅，C未晋级 | 用完整问答、原文及工具事实审核支持性、适用性和个人事实/文献边界，记录审阅者和分歧；不以工程通过替代 |
| 部署与运维 | 本轮为loopback和独占worker；没有压力测试、真实网关、密钥轮换或在线删除 | 单实例预发先验证TLS/服务身份、超时与限流、恢复/回滚、删除保留政策；授权库与每次连接的容量需实测 |
| 依赖基线 | .NET构建实际报告SQLitePCLRaw.lib.e_sqlite3 2.1.6的NU1903高严重性告警，来自当前后端依赖；另有可空性警告 | 在确定的后端版本中审核/更新依赖并重新构建验收，不把隔离宿主成功构建视为生产依赖合格 |

生产设计应保持“个人记录在受控域处理，外部模型仅用批准文献与固定概念”的现有边界。真实患者影子试点还需要明确数据处理授权、供应商策略及保留/删除规则；当前证据不足以批准接入。

## 复现与证据

从RAG仓库运行，输出必须是新目录：

```powershell
D:/Project/metaAgent/.venv/Scripts/python.exe -X utf8 tools/eval_shared_authority.py --meta-repo D:/Project/metaAgent --backend-repo D:/Project/AI_WebApi --output .local/shared_authority_v3/new-attempt
.venv/Scripts/python.exe -X utf8 tools/verify_hybrid_postgres.py --output .local/shared_authority_v3/new-suite --meta-repo D:/Project/metaAgent
.venv/Scripts/python.exe -X utf8 tools/audit_shared_authority.py --output .local/shared_authority_v3/new-audit
```

复现依赖Docker、本机.NET 10 SDK、当前AI_WebApi契约源码及两个Python环境。所有配置中的令牌均为临时合成值，仅保留在私有`.local/shared_authority_v3`；不读取现有后端数据库配置。机器结果在[汇总](../artifacts/shared-authority-v3/summary.json)和[交付审计](../artifacts/shared-authority-v3/delivery-audit.json)。生产镜像、运行容器和已有内部试点保持原状态。
