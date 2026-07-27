# GoalEvolve：可复现的 OpenROAD 源码进化

GoalEvolve 对 OpenROAD 中有边界的 C++ 机制进行进化；每个候选均经过 post-route flow 和固定的 4/4 checker 验证，并保留晋升所需的证据。工程将依赖、实现、实验和结果清晰分离，但不是训练模型工程。

```text
Goal contract → 诊断 → 检索 / Teacher 规划 → Student C++ 修改
→ 隔离 build 与 post-route 评测 → evidence / EPD → parent 晋升
```

## 目录

```text
goalevolve/                可执行实现
  core/ planning/ agents/ execution/ evaluation/ testing/
config/                    schema 与可移植模板
experiments/               design 专用的受审 profile 和预期 QoR
artifact_evaluation/       AE-1/AE-2 manifest、冻结源码和预期证据
third_party/               固定的官方 parser/checker、ASAP7 与 contest 输入
toolchain/                 版本锁和环境说明
tests/                     unit、integration、artifact 检查
outputs/                   被忽略的 build、session 与 campaign
```

`goalevolve/cli.py` 是真实 CLI 实现，不存在兼容转发模块。冻结源码 artifact 与新 campaign 严格分离：前者是 AE-2 输入，绝不能成为可变 Student workspace。

## 三类 artifact evaluation

| 类型 | 证明内容 | 是否使用 API key | 是否有稳定 pass/fail |
|---|---|---:|---:|
| AE-1 | 源码、benchmark、checker、manifest 与本机接口完整 | 否 | 是 |
| AE-2 | 固定 AES R54 Student 1 源码可重新 build 并 post-route replay | 否 | 是，使用明确容差 |
| AE-3 | 用户能启动新的 Teacher/Student 源码进化 campaign | 是 | 只检查流程；QoR 本身随机 |

立即运行 AE-1：

```bash
cd /path/to/GoalEvolve
PYTHONPATH=. python3 -m artifact_evaluation.runner ae1
```

运行确定性的 AE-2（build 会消耗较长时间和较大磁盘）：

```bash
PYTHONPATH=. python3 -m artifact_evaluation.runner ae2 --artifact aes_r54_student1 --rebuild --jobs 8
```

AE-2 会重建 `artifact_evaluation/lineage/aes_cipher_top/r054_student1/source`，执行可移植的已捕获 Tcl、官方 parser 与 4/4 checker，然后与 `artifact_evaluation/expected/aes_cipher_top/r054_student1/metrics.json` 对比 TNS、dynamic power、leakage。该结果的阶段是 `global_route + estimate_parasitics`，不是 detailed routing。

## AE-3：新鲜进化

从 [config/credentials/goalevolve_codex.env.example](config/credentials/goalevolve_codex.env.example) 建立被忽略的 `config/credentials/goalevolve_codex.env`，并执行 `chmod 600 config/credentials/goalevolve_codex.env`。GoalEvolve 永远不读取 `~/.codex`，而是从这个项目文件创建隔离的 Teacher/Student home。提供的 AE-3 profile 使用 `gpt-5.6-terra` 和 `xhigh`。

## Design Profile

项目现已携带全部八个 contest design 的输入：`aes_cipher_top`、`ariane`、`jpeg_encoder`、`mempool_group`、`nvdla_a`、`nvdla_c`、`nvdla_m`、`nvdla_p`。每个 design 的 `.def(.gz)`、Verilog、SDC 与官方初始 metrics 位于 `third_party/benchmarks/benchmarks/`；它们共享 `artifact_evaluation/lineage/openroad_power/p0/` 的 source-only OpenROAD p0 快照，并以全树内容 hash 标识。

新 design 运行 campaign 前，必须先对该 design 的同一 source/flow 执行 baseline，并将测得 metrics 冻结到 evolution profile：

```bash
PYTHONPATH=. python3 -m goalevolve.cli baseline \
  --config experiments/contest2026/<design>.bootstrap.json \
  --output outputs/baseline/<design>
```

该命令在 `outputs/` 创建私有 build，不依赖外部 OpenROAD checkout 或预编译 binary。只有 baseline 对该源码快照和 flow 有效后，才能运行 `goalevolve run`。

先验证一轮真实 Teacher/Student：

```bash
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/aes_cipher_top/ae3_smoke.json --rounds 1
```

正常四 Student 实验使用 `experiments/aes_cipher_top/evolve.json`。新鲜 campaign 只写入 `outputs/`；不得要求它精确复现 R54 数值。

## 当前固定 AES artifact

`aes_r54_student1` 是 `round_054:student_1`，source hash 为 `d826c042…f3bc`。已验证 post-route 指标：TNS `15.79 ns`、dynamic power `335.9714B pW`、leakage `28.6M pW`、zero DRV、官方 4/4 pass。SPPA `38.1775441168` 与 Sfinal `14.0356761445` 仅为 observer，绝不参与检索或晋升。

## 本地验证快照

2026-07-27，本独立 artifact 目录已在记录的本机 toolchain 上完成验证：

| 检查 | 结果 |
|---|---|
| Unit/artifact tests | `101 passed` |
| AE-1 预检 | passed；`openroad_on_path=false` 可接受，因为 AE-2 会 build 冻结源码 |
| AE-2 固定复验 | passed；使用重建的 `outputs/ae2/aes_r54_student1/build/bin/openroad`，TNS `15.79 ns`、dynamic `335.9714B pW`、leakage `28.6M pW`、官方 4/4 pass |
| AE-3 smoke campaign | 完成一轮真实 `gpt-5.6-terra` Teacher/Student，包括同一 Student telemetry repair、rebuild、flow、metrics、官方 4/4 与 Teacher review |

AE-3 smoke 候选被正确 refute，而不是 promote：它激活了目标机制（`rmp_path_cone_examined=6`、`rmp_timing_examined=8`）并通过完整性检查，但结果为 TNS `15.80 ns`、dynamic `340.9712B pW`、leakage `28.8M pW`，normalized goal distance 略差。这是流程通过，不是固定 QoR 声明。

复现结论前先阅读 [artifact_evaluation/README.zh-CN.md](artifact_evaluation/README.zh-CN.md)；变更版本前阅读 [toolchain/README.zh-CN.md](toolchain/README.zh-CN.md)；实现导图见 [goalevolve/README.zh-CN.md](goalevolve/README.zh-CN.md)。
