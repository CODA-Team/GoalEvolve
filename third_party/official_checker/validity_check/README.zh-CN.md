# 官方 reference 4/4 有效性检查器

该目录是官方 contest validity checker 的快照。`def_validity_check.py` 编排四项检查；`flipflop_check.py` 实现 DFF/时钟检查；`OpenROAD_utils.tcl` 写出所需的 node/net 文件；CSV 列出允许移动的等价标准单元家族。

`goalevolve/evaluation/contest2026.py` 在导出 post-route artifact 后直接调用这些文件。只有 checker 成功结束并输出 `SUMMARY: 4/4 checks passed`，candidate 的 `lec` evidence slot 才能通过。该门还必须与私有 build/flow 成功、冻结 QoR 指标解析和零 DRV 验证共同成立，不能互相替代。
