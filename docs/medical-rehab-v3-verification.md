# 医学第三轮验证记录

日期：2026-10-10。范围：`medical_rehab_v3` 独立离线检索实验；没有激活生产索引、生成临床回答或核验在线患者数据库。

## 实施结果

完成统一事实复核、仅依问题与文献元数据的规划、保留主题的检索、子问题召回、按维度选证据、固定长度上下文包、相邻原文扩展及缺输入路由。沿用 8 份 PDF、102 页、736 块。开发 52 题、计分 41 题；新增保留题 20 题、计分 16 题，运行一次后保留结果。

开发选择为 `context_expanded`。固定 v3 Gold 和 4000 cl100k_base 参考 token 预算内，普通重排开发完整覆盖 33/41，新方案 38/41；保留题分别 12/16、15/16。旧版来源路由重排组合保留题也是 15/16。Top-5 与预算内结果分别发布，不能把 16/16 的 Top-5 覆盖解释为临床回答准确率。

## 检查结果

| 检查 | 实际结果 |
| --- | --- |
| 全量测试 | 169 passed、33 skipped；33 项为未连接数据库的集成测试，另有 2 条依赖弃用警告 |
| Ruff 检查及格式 | 全部通过，100 个 Python 文件格式通过 |
| 报告完整性 | 两个 split 的规划、覆盖选择、原文映射、上下文包、参考 token 预算、逐题和聚合指标重算通过 |
| 负向报告校验 | 仅在内存副本中伪造领域、指标、来源区间、token 数和问题规划，5 类均被拒绝；真实报告未修改，未调用模型 |
| 原医学实验保全 | 运行前基线中的 41 个旧文件哈希保持一致；唯一有意变化为实验注册表增加 v3 |
| 元宇宙文件保全 | 12 个受保护文件哈希一致 |
| PDF 备份 | 全部 180 份 SHA256 一致；入选 8 份、未入选 172 份，未加入 Word 或新 PDF |
| 上下文重复区间 | 全部实际开发和测试包未发现同页重叠卡片 |
| 构建 | `uv build --no-sources` 成功生成源码包及 wheel |
| 构建内容检查 | wheel 与源码包均无 PDF、私有语料/缓存、备份来源清单或真实凭据；允许公开 `.env.example` |
| 工作区差异 | `git diff --check` 通过；现有更改未提交或推送 |

## 复核入口

```powershell
.venv/Scripts/python.exe tools/report_medical_v3.py
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/python.exe -m pytest -q
uv build --no-sources
```

报告重算不会重新打开保留题或请求模型服务。完整输入和证据依赖本地被忽略的 PDF、冻结语料及历史实际运行记录；公开代码包本身不含这些正文，无法凭公开题集单独复现全部结果。

保全哈希基线在 `.local/medical_rehab_v3/baseline_integrity.json` 和 `.local/medical_rehab_v1/legacy_integrity.json`；原始结果在 `.local/medical_rehab_v3/results/{dev,test}/`。公开[实验流程](../eval/medical_rehab_v3/README.md)和[实测结果](../artifacts/eval/medical_rehab_v3/README.md)说明测试口径、调用成本与局限。
