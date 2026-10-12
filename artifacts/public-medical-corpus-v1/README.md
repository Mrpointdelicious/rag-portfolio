# Public Medical Corpus v1 技术审查包

`manifest.json`：两层语料数量、release、scope、源文件 hash 与许可；`public-prose.json`：仅获审 CC BY 4.0 prose 及署名，所有 clinical_approval=false。原 PDF/XML、未知权利全文及向量不在公开包中。

`rights_audit.json`、`dedup_report.json`、`release_changes.json`：资源权利、作品去重、引用重叠和作者更正；`pdf_xml_alignment.json`：真实辅助 PDF 页对齐，不改变 XML page=null。

`eval_summary.json`、`dev_selection.json`、`failure_cases.json`：固定六路实时评测、开发选型、真实失败；`agent_review.json`：MetaAgent 最终回答和技术源文审阅，独立人工/临床评审 pending。

`index_manifest.json`：实际公开 Dense 索引指纹及 provider 批请求记录；`combined_index_manifest.json`：本地 BM25 实际检查，合并库外部模型处理仍 BLOCKED；`preservation.json`：旧157文件不变；`package_audit.json`、`acceptance.json`：实际验证和未完成项。

查看 [设计与复现](../../docs/public-medical-corpus-v1-design.md)、[权利审核](../../docs/public-medical-corpus-v1-rights-review.md)、[EVAL](../../docs/public-medical-corpus-v1-eval.md)。所有失败轨迹本地保留，未按最好结果择优删记录。
