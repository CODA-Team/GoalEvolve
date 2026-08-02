# 配置

`schema/experiment.schema.json` 描述可审阅的顶层字段；`templates/` 保存可移植起点；`experiments/` 保存 design 专用 profile。相对路径相对 profile 文件解析。profile 可用浅层 `extends` 继承 experiment 树中的另一个受审 profile。

每个 profile 必须写入 `baseline_metrics` 和绝对值 `target_metrics`；运行时不会再用比例计算目标。`state_root` 是可选项，缺省路径为 `outputs/ae3/<design>/`。

`epd_max_reinforcement_attempts` 是一个 `promising` 实测 idea 可交给 Enhancer 的最大强化次数，默认
为 `2`。该值会写入每个新建 EPD idea；它不限制 Explorer 或 Integrator 的尝试次数。

`source_root` 是可选项。省略或写为 `null` 时，`contest_openroad` profile 使用共享
源码快照 `artifact_evaluation/lineage/openroad_power/p0/source`；设置
`GOALEVOLVE_OPENROAD_SEED` 可替换该机器上的默认值。若某个 design 需使用专用源码，
必须在该 design 的 `baseline.json` 与 `evolve.json` 中写入完全相同的
`source_root`。它必须指向含 `CMakeLists.txt` 的 OpenROAD 源码根目录，不能指向已经
build 的 `openroad` 二进制。

新 design 时，将 `templates/design.baseline.example.json` 和
`templates/design.evolve.example.json` 分别复制为
`experiments/<design>/baseline.json`、`experiments/<design>/evolve.json`。
先执行 baseline，它会写入 `outputs/baseline/<design>/baseline.json`；将测得的
决策指标填入 `evolve.json` 的 `baseline_metrics`，设置绝对目标，并将
`campaign_ready` 改为 `true`。evolve 模板初始锁定，避免 placeholder 指标误启动
campaign。

`codex.json` 是提交到仓库、所有 design 共用的 Teacher/Student 模型、推理强度、重试、超时、Student 修复次数和 Teacher Markdown 格式返工次数策略。worker home 在每个 campaign 的 `state_root` 下创建。`credentials/goalevolve_codex.env` 仅保存本地 API key 与 provider 连接信息，且被 Git 忽略。机器相关默认值使用 `GOALEVOLVE_OPENROAD_SEED`、`GOALEVOLVE_BENCHMARK_ROOT`，或写入受审的相对路径 profile。
