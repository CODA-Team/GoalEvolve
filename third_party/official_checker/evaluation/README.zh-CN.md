# 官方指标解析器

`parse_log.py` 是从 reference 官方 evaluation package 原样 third-party 的日志解析器。`goalevolve/evaluation/contest2026.py` 在 post-route OpenROAD flow 后调用它，将 CSV 中的结果写入配置定义的冻结指标（当前 AES profile 例如为 TNS、dynamic power、leakage power）。

它不是 GoalEvolve 的 planner、normalized-gap 公式、Sfinal/SPPA 公式或 promotion policy；Sfinal/SPPA 由 `goalevolve/evaluation/sfinal.py` 独立计算，且仅用于 observer comparison。如需改变目标定义，应修改 `contracts.py` 与实验配置，而不是修改本目录中的官方解析器。
