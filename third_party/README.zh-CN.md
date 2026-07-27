# `third_party/`：随 v2 固定发布的外部检查实现

该目录只包含运行 v2 必需、并且需要锁定版本的第三方或官方检查代码。它不是 GoalEvolve 搜索空间的一部分；升级必须作为显式 third-party 更新，记录清楚来源和版本。

当前的 `official_checker/` 从本机 reference Contest Scripts & Benchmarks 的 `evaluation/validity_check` 与 `evaluation/parse_log.py` 机械导入。它负责从 candidate 产物中解析指标、检查 node/nets/Verilog 完整性；不负责定义 GoalEvolve 的 frozen QoR gap、power-first 分阶段、retrieval 或 promotion 语义。
