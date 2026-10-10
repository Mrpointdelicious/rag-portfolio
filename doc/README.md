# 原始 PDF 素材

`selected/` 保存首批 8 份候选原始 PDF；`unselected/` 保存盘点中的全部其余 PDF，包括后续康复候选、方法论文和其他领域资料。

文件以资料 ID、原始标题和文件 SHA256 前缀命名。完全相同副本只保存一份，所有原路径、压缩包成员和 Zotero 标识保留在本地 `backup_manifest.json`；`backup_index.md` 提供可点击目录。每个来源副本和备份文件都按完整 SHA256 校验，原文件和压缩包保持原样。

备份不等于纳入医学检索。实验只读取显式选定的原始 PDF，Word 整理稿与说明文件不进入语料。方法论文和其他领域 PDF 虽有备份，仍在首批检索集合之外。

PDF 正文和包含本机来源路径的索引不进入 Git，也不进入应用 Docker 镜像。医学实验的公共配置位于 `eval/medical_rehab_v1/`，本地提取结果和运行记录位于 `.local/medical_rehab_v1/`。
