# CC0 技术演示语料

本目录两个文档是本 Integration Spike 自建的虚构研究方案，按 CC0-1.0 许可提供。40/30 人、6/8 周等均为方案设定，不是真实试验数据、患者数值或临床结论。`approved=true` 只表示允许技术演示；`clinical_approval=false` 始终保留。独立 `technical_demo_only` 服务拒绝 production、patient、临床角色及带患者上下文的请求。

`corpus.json` 是原始文档本身；start/end 为其中 document.text 的 Python Unicode 字符区间 `[start,end)`，不是字节偏移或 JSON 文件偏移。页码未知，返回 null。source 的 repo://demo/protocol-a 与 protocol-b 在 corpus.documents 中唯一定位，doc_ref 再定位版本和 section_id。不是可访问的网页 URL。

审核这些材料后，操作者必须显式配置 corpus.sha256 中的 SHA256，语料改变须重新审核并更新 pin；运行时不接受调用者上传正文或自行扩大知识库。
