# Medical Business EVAL v1

40个来源编排的新业务条件，24 dev / 16 heldout，8类各5条件，共45轮。用户题文不提供NEWxx/Dxxx内部代号；多轮条件沿用前文指代。题目在任何本轮检索前封存，见 `seal.json`。

公开文件只有自编题文、目标行为、领域与故障条件。59个原文锚点及相关全文只保存在忽略目录 `.local/medical_business_v1/gold/`；旧资源使用确认没有被改写为公开转载许可。公开checkout不包含完整合并库或私有Gold，不能仅凭公开题目宣称完整复现。

固定已有默认 `hybrid_rerank`，没有开发选型或针对已知12题调参。先运行dev，再运行一次heldout；保留集与dev来自同一批已审阅文献，不代表未见来源外推。题目、Gold及完整答案语义仍待独立人工评审；clinical_approval=false。

离线检索使用Top-5 / 4000 cl100k token；多轮查询仅拼接此前用户题文和当前问题，不使用模型回答或Gold扩展。MetaAgent沿用现有单Agent及business-grounded-v9，检索服务预算为12000 UTF-8字节上界；两条链路分别报告。

空结果、503、超时为明确故障注入；过期条件由测试进程删除证据。知识不足/权限/故障和有证据题分报。产品帮助未装入本候选，相关题只验证边界解释，不能据此宣称产品FAQ验收完成。患者上下文只有自建文字条件，真实患者远程知识路径未运行。

原50题与12题子集是另一路固定回归，不计入本轮新保留成绩。[结果](../../docs/medical-business-v1-results.md) · [运行方法](../../docs/medical-business-v1-runbook.md)。
