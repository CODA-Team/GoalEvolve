# 配置

`schema/experiment.schema.json` 描述可审阅的顶层字段；`templates/` 保存可移植起点；`experiments/` 保存 design 专用 profile。相对路径相对 profile 文件解析。同目录 profile 可用浅层 `extends` 继承另一个 profile。

不得在此目录保存 key、build 输出或旧 runtime 路径。机器相关默认值使用 `GOALEVOLVE_OPENROAD_SEED`、`GOALEVOLVE_BENCHMARK_ROOT`，或写入受审的相对路径 profile。
