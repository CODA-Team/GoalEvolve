# MLCAD 2026 官方检查快照

来源为官方 contest 工程中的 `evaluation` 目录。v2 固定 third-party 了最小必需集合：

- `validity_check/def_validity_check.py`：官方四项有效性检查编排。
- `validity_check/flipflop_check.py`：DFF 数量和时钟连通性检查。
- `validity_check/OpenROAD_utils.tcl`：从 OpenROAD 数据库导出 `node.csv` 与 `nets.csv`。
- `validity_check/asap7_equivalent_cell_list.csv`：官方等价 cell 表。
- `evaluation/parse_log.py`：official evaluation log 指标解析器。

四项检查分别覆盖固定 physical cell、macro、I/O 位置和 flip-flop integrity。v2 只有在脚本返回 0 且出现 `SUMMARY: 4/4 checks passed` 时，才把 candidate 的 `lec` 槽位标为通过。

这些上游代码按原样 third-party，不应在这里进行算法性修改；GoalEvolve 的 frozen QoR gap、power-first staging、检索和晋升策略变化应发生在 package 实现中。
