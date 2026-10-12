# 合并医学知识库与业务基线 v1

此目录保留新的技术汇总、版本切换验证、原题回归、引用轨迹检查及有限技术审阅。原始模型消息、完整回答、向量、旧全文和59个原文锚点均在被忽略的 `.local/medical_business_v1/`。

- `eval_summary.json`：新40条件/45轮的分项结果，检索与模型可见覆盖分别报告。
- `agent_trace_audit.json`：逐轮来源一致性、可见原句区间与引用成员检查；不是语义准确率。
- `technical_review.json`：16条执行后的有限技术标注；不覆盖全部额外陈述。
- `fixed_regression_report.json`：原50题与已知12题子集，保留原基线和逐题变化。
- `release_manifest.json` / `release_verification.json`：14篇/713块、独立索引、候选切换、撤销与回滚。
- `run_fingerprints.json`：原始执行记录与新题集的指纹。
- `prior_regressions.json`：旧C/B及E02/E09的实际结果，保留原规则与既有勘误规则的区别。
- `preservation.json`：历史157文件与本轮开始时48文件的核对；只允许回归说明和执行登记更新。
- `source_fingerprints.json`：候选源代码、MetaAgent版本与运行配置的指纹。
- `acceptance.json`：交付检查、实际测试数量及尚未通过完整业务验收的原因。
- `package_audit.json`：真实wheel/sdist和Docker COPY上下文检查；没有把上下文镜像当作应用容器运行验收。

技术候选与基线已交付；完整业务验收PARTIAL，独立人工/临床语义NOT_ASSESSED。复现与限制见[运行方法](../../docs/medical-business-v1-runbook.md)和[结果](../../docs/medical-business-v1-results.md)。
