# 合并医学候选发布与评测运行方法

工作目录：`D:\Project\rag-portfolio`。已有模型配置由程序读取，不放进命令或报告。所有本轮服务均为loopback隔离实例，正式API/数据库/MetaAgent默认配置未切换。

## 输入与固定版本

- 合并语料：`.local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json`，14作品/713块。
- 使用确认：`.local/medical_business_v1/processing_approval.json`，逐资源hash绑定，不代表公开转载或临床批准。
- 合并索引：`.local/medical_business_v1/indexes/combined-business-dense-1.json`。
- 注册表：`.local/medical_business_v1/releases/registry.json`；最终active为 `combined-business-v1`。
- 旧公开索引：`.local/public_medical_corpus_v1/indexes/medical-public-v1-dense-1.json`，作为独立回滚来源。
- 新题：`eval/medical_business_v1/{dev,heldout}.jsonl`；本地原文锚点在 `.local/medical_business_v1/gold/`。

公开checkout没有合并全文、使用确认或私有Gold；缺这些输入时不能宣称完整复现。已存在的语料、题目、索引与运行输出不覆盖；新构建需新文件名，新运行需新空目录。

## 版本生命周期

`tools/medical_release.py`支持index/register/validate/activate/revoke/rollback/show。注册ID不可覆盖；候选先通过一致性校验再激活；撤销后不能重新激活同一ID。回滚目标必须已校验且曾经激活。注册表使用本地单写者锁、generation检查和原子文件替换，不能当作多副本生产发布控制面。

查看本轮状态：

```powershell
.venv\Scripts\python.exe -X utf8 tools/medical_release.py show
```

在新的独立注册表中注册一组候选的例子：

```powershell
$taskRegistryPath = '.local/medical_business_v1/reproduction/releases.json'
.venv\Scripts\python.exe -X utf8 tools/medical_release.py register --registry $taskRegistryPath --release-id public-baseline --corpus artifacts/public-medical-corpus-v1/public-prose.json --index .local/public_medical_corpus_v1/indexes/medical-public-v1-dense-1.json
.venv\Scripts\python.exe -X utf8 tools/medical_release.py validate --registry $taskRegistryPath --release-id public-baseline
.venv\Scripts\python.exe -X utf8 tools/medical_release.py activate --registry $taskRegistryPath --release-id public-baseline
.venv\Scripts\python.exe -X utf8 tools/medical_release.py register --registry $taskRegistryPath --release-id combined-business-v1 --corpus .local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json --index .local/medical_business_v1/indexes/combined-business-dense-1.json --approval .local/medical_business_v1/processing_approval.json
.venv\Scripts\python.exe -X utf8 tools/medical_release.py validate --registry $taskRegistryPath --release-id combined-business-v1
.venv\Scripts\python.exe -X utf8 tools/medical_release.py activate --registry $taskRegistryPath --release-id combined-business-v1
```

校验是文件、索引和使用范围一致性检查；真实provider验证由 `tools/verify_medical_release.py`另跑。该工具需要上述两个ID，创建自有撤销探针，最后恢复合并候选。其输出必须选择新文件。

```powershell
.venv\Scripts\python.exe -X utf8 tools/verify_medical_release.py --registry $taskRegistryPath --output .local/medical_business_v1/reproduction/release-check.json
```

服务入口 `tools/serve_medical_release.py`只绑定127.0.0.1，使用独立临时token。请求必须匹配tenant/scope `medical-business-v1`、operator、无患者上下文，以及当前有效语料版本和双pin；旧pin返回409，不自动转向其他来源。回滚后客户端也须显式使用旧pin，本轮没有修改MetaAgent.env实现切换。

## 回归与业务基线

固定50题回归已经接入专门入口，不重新选型：

```powershell
.venv\Scripts\python.exe -X utf8 tools/eval_medical_business.py regressions --corpus artifacts/public-medical-corpus-v1/public-prose.json --index .local/public_medical_corpus_v1/indexes/medical-public-v1-dense-1.json --output .local/medical_business_v1/regressions/reproduction-1
```

这会实时查询全部50题，核对原seal，单列12题的基线差异。重复后始终称已打开题回归；不能把它当新盲测。新合并库需另行复核锚点映射，不能静默套用公开库锚点。

新业务首次运行用 `tools/eval_medical_business.py dev|heldout`，指定合并corpus、index、approval与新输出目录。Agent用MetaAgent解释器运行 `tools/medical_business_agent.py`，指定两个仓库、注册表、split与输出目录；它自行创建并清理隔离TCP服务和临时token。

```powershell
D:\Project\metaAgent\.venv\Scripts\python.exe -X utf8 tools/medical_business_agent.py --meta-repo D:\Project\metaAgent --rag-repo D:\Project\rag-portfolio --registry D:\Project\rag-portfolio\.local\medical_business_v1\reproduction\releases.json --split dev --output D:\Project\rag-portfolio\.local\medical_business_v1\reproduction\agent-dev
```

本轮已完成dev和heldout各一次；此后重复只算独立复现/已打开回归。没有改变候选去追求这份保留分数。故障注入只在显式测试参数下开启；fault文件与预期标签不进入模型提示或检索排序。

原始输出保留问题、实际查询、模型调用、最后可见消息、工具结果、Evidence、来源与调用元数据。`report_medical_business.py`从本轮固定目录汇总，`review_medical_business.py`保存有界技术标注；不再调用模型判分。

## 验证

```powershell
.venv\Scripts\ruff.exe check src tools tests
.venv\Scripts\ruff.exe format --check src tools tests
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_medical_release.py tests/test_public_medical.py -q
.venv\Scripts\python.exe -X utf8 tools/verify_hybrid_postgres.py --output .local/medical_business_v1/verification/reproduction-db
.venv\Scripts\python.exe -X utf8 tools/verify_public_medical_package.py --local .local/medical_business_v1/reproduction-package --output .local/medical_business_v1/reproduction-package/audit.json
```

数据库脚本只创建并清理自有临时容器；打包验证实际构建包并检查自有Docker COPY上下文，不声称部署应用容器。语义、引用成员、任务完成与临床批准分别报告，见[本轮结果](medical-business-v1-results.md)。
