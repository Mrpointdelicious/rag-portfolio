# Public Medical Corpus v1：作品与资源权利审核

日期：2026-10-12。审核为 Codex 技术审核，独立法律/临床审核待完成。逐资源下载记录、时间、作者、URL、许可 XML、原文件 SHA256 和第三方 credit 行见 `artifacts/public-medical-corpus-v1/rights_audit.json`。此文不替代该清单。

## 旧八篇

| ID | DOI | 版本/发表 | 本地资产与公开决定 |
|---|---|---|---|
| D001 | 10.3724/SP.J.1329.2024.03001 | 2024 / 2024 | 本地版式无明确许可，HOLD。出版方 VOR 为 BY-NC-ND，不推定本地版式或规范化派生获许可 |
| D003 | 10.16305/j.1007-1334.2024.2401120 | 2024 / 2024 | 已读首页及许可线索，license_unknown，HOLD |
| D041 | 10.3969/j.issn.1001-1242.2025.09.001 | 2025 / 2025 | 潘钰等执笔，license_unknown，HOLD；D102 是同作品重复文件 |
| D040 | 10.3969/j.issn.1006-9771.2012.04.001 | 2011完整版 / 2012 | license_unknown，HOLD；版次与发表年分开 |
| D042 | 10.7507/1672-2531.202306145 | 2023版 / 2024 | license_unknown，HOLD；疾病为膝骨关节炎 |
| D129 | 10.1016/j.rehab.2022.101674 | 在线2022 / 刊期2023 | Elsevier all rights reserved；免费阅读不等于允许再分发，HOLD |
| D161 | 10.3390/brainsci13010092 | 2023 / 2023 | 首页明确 CC BY 4.0；重新取得官方 XML，公开仅筛选后的 prose；原 PDF 保留本地 |
| D173 | 10.23736/S1973-9087.21.06887-8 | 2021 / 2021 | Minerva 版权声明，无本地再分发许可，HOLD |

所有旧 PDF 实际存在，逐一读了首页/末页和元数据，并渲染首页题名/作者区域核对。D041 两文件各 9 页，二进制 hash 不同，但去空白全文 SHA256 完全相同：`7167ff5a57e71ddb93da2272b8a62870393aa361265ef28cfc061881bf584fda`。不将 D102 计作第十五篇，保留 D041 原文件与旧实验不变。这个结果确认抽取正文相同，不宣称两文件图像、全部字节或所有出版历史完全相同。

出版社/官方记录：[D001 出版页](https://cjournal.hep.com.cn/2096-0328/CN/1217460202854224019)、[D040 发表记录](https://www.cjrtponline.com/CN/Y2012/V18/I4/301)、[D042 出版记录](https://www.cjebm.com/article/10.7507/1672-2531.202306145)、[D129 出版页](https://www.sciencedirect.com/science/article/pii/S1877065722000471)、[D173 出版页](https://www.minervamedica.it/it/riviste/europa-medicophysica/articolo.php?cod=R33Y2021N03A0460)。D003、D041 的许可未能从已审资源确定，未补写许可。

## 新六篇及 D161 新格式

六个指定 DOI 全部真实获得 PDF + XML，新增论文为 6 篇而不是 12 篇；D161 补取两种格式另计资源。NEW01/02/06 与 D161 通过 PMC 官方 Cloud 的已发表版本资源获得，NEW03/04/05 通过 PLOS 官方 printable/manuscript 入口获得。PLOS XML 的官方 302 跳转首次未跟随导致失败，记录保留；后续仅允许其出版社存储路径，签名参数不写入清单。未绕过 MDPI 页面限制。

PMC 当前说明推荐官方数据通道，并已迁移传统 OA API/FTP 工作流；本次使用许可的 Cloud 而非网页批量抓取。见 [PMC 自动获取政策](https://pmc.ncbi.nlm.nih.gov/tools/textmining/) 与 [PMC Cloud](https://pmc.ncbi.nlm.nih.gov/tools/cloud/)。收集器只查询指定 PMCID 前缀，记录 `.1` 资源版本、DOI/PMCID、文件类型、字节量、SHA256；未下载整桶。

| ID | DOI | 实际 PDF 页 | 资源审查后公开内容 |
|---|---|---:|---|
| NEW01 | 10.3390/medicina60040620 | 17 | XML abstract/body prose，CC BY 4.0 |
| NEW02 | 10.3390/bioengineering10050585 | 13 | 同上 |
| NEW03 | 10.1371/journal.pone.0229707 | 19 | 同上；照片 credit 与同行评审 sub-article 排除 |
| NEW04 | 10.1371/journal.pone.0297074 | 14 | XML abstract/body prose，CC BY 4.0 |
| NEW05 | 10.1371/journal.pone.0328930 | 11 | 同上 |
| NEW06 | 10.3390/jcm13216616 | 28 | 同上；补充重叠矩阵未再分发 |

机器许可节点、PDF 许可声明、题名/DOI、作者及第三方 credit 已检查，新增六篇首页已渲染核对。CC BY 仅用于经筛选 prose 派生；所有原 PDF、全 XML、图表、附件仍为 HOLD，不随 Git、sdist/wheel 或 Docker 发布。保留作者署名、DOI、[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)、获取来源、资源指纹及修改说明。PLOS 的文章许可不免除第三方材料许可义务，见 [出版社许可政策](https://journals.plos.org/plosone/s/licenses-and-copyright)。

已检查下载 JATS 的 article-type、related-article 及可用 Cloud retracted 字段；未发现资源中指示的撤稿/更正通知。这是固定下载资产检查，不是全网撤稿监测，也不推定未来版本不会变化。资源哈希变化时拒绝静默替换，需重新审核并建立新 release。

## 证据重叠与人工 Review

`dedup_report.json` 根据原始 JATS reference DOI 记录 NEW01→D161、NEW02→D173、NEW06→D173、D161→NEW03。引用关系不证明纳入同一受试者，也不证明受试者互不重叠；没有把综述与 RCT 人数相加。NEW06 原文自身区分重叠前后计数，不将其数值用作本项目的独立患者总量。`independent_cohort_count=null`。

请重点核对：D041/D102 同作品的判断、未知许可旧文献的 HOLD、D001 不同版式/VOR 的许可边界、D161 与新六篇 prose 的第三方内容排除。没有独立法律复核签名，也没有任何患者端临床批准。
