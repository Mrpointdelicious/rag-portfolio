# 按知识领域选择实验

元宇宙场景与医学文献分别维护题集、Gold、语料、索引和结果。命令必须显式选择实验：

```powershell
uv run --locked python tools/eval_experiment.py metaverse_dense_v1 --help
uv run --locked python tools/eval_experiment.py metaverse_v2 --help
uv run --locked --extra medical python tools/eval_experiment.py medical_rehab_v1 --help
uv run --locked --extra medical python tools/eval_experiment.py medical_rehab_v2 --help
uv run --locked --extra medical python tools/eval_experiment.py medical_rehab_v3 --help
```

| 领域 | 实验 | 公开题集 | 私有工作目录 | 结果说明 |
| --- | --- | --- | --- | --- |
| 元宇宙场景 `metaverse_scenes` | `metaverse_dense_v1` | `hospital_dense_v1/cases.json` | `.local/eval/hospital_dense_v1/` | 原 5 文档、43 块、29 题 |
| 元宇宙场景 `metaverse_scenes` | `metaverse_v2` | `yueyang_multidoc_v2.json` | `.local/eval/multidoc_eval_v2/` | 复用原真实 Dense Top-5 的离线对照 |
| 医学知识 `medical_knowledge` | `medical_rehab_v1` | `medical_rehab_v1/cases.json` | `.local/medical_rehab_v1/` | 原始 PDF、40 题、真实 Dense Top-20 与本地 BM25/RRF |
| 医学知识 `medical_knowledge` | `medical_rehab_v2` | `medical_rehab_v2/cases.json` | `.local/medical_rehab_v2/` | 同一冻结 PDF、40 开发题、12 新保留题、7 路真实重排/路由对照 |
| 医学知识 `medical_knowledge` | `medical_rehab_v3` | `medical_rehab_v3/cases.json` | `.local/medical_rehab_v3/` | 同一冻结 PDF、52 开发题、20 新保留题、问题拆解/维度覆盖/统一预算对照 |

历史场景文件保留原路径和内容；文件名中的 hospital/yueyang 不代表本次医学文献效果。医学实验拒绝其他领域的 schema、路径、缓存和索引 payload。两领域指标不可混合汇总。

医学流程见 [独立实验说明](medical_rehab_v1/README.md)，实测结果见 [医学结果](../artifacts/eval/medical_rehab_v1/README.md)。

第二轮见 [medical_rehab_v2](medical_rehab_v2/README.md)及[聚合结果](../artifacts/eval/medical_rehab_v2/README.md)。v2 只读复用 v1 文献源与冻结语料，Gold、缓存、索引 payload、开发选择与保留题结果均独立。

第三轮见 [medical_rehab_v3](medical_rehab_v3/README.md)及[实测结果](../artifacts/eval/medical_rehab_v3/README.md)。已暴露题转开发题，新保留题仅打开一次；固定 Top-5 与 4000 参考 token 预算分别评估。标注修订收益单列，缺患者或实时输入题单独检查。
