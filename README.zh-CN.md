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
Makefile                   环境 doctor、setup 和 AE-1 check 入口
scripts/human/             Makefile 环境命令的实现
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

## 环境安装

使用 Linux 主机。固定源码和工具链 provenance 保存在
[`toolchain/lock.json`](toolchain/lock.json)；AE-2 会重建仓库内 OpenROAD 源码，
不需要系统预装 `openroad` 二进制。

```bash
git clone --branch artifact https://github.com/Liu7541/GOAL_EVOLVE.git
cd GOAL_EVOLVE

# 检查主机命令、冻结源码和依赖安装器。
make doctor

# 创建 .venv 并安装 pytest；该命令不使用 sudo。
make setup

# 需要时显式安装冻结 OpenROAD 的系统/通用依赖。
make setup INSTALL_SYSTEM_DEPS=1 JOBS=8

# 为 AE-3 安装或更新 Codex CLI；该命令不会配置 API key。
make setup INSTALL_CODEX_CLI=1

# 执行主机检查和 AE-1 预检。
make check
```

`make doctor` 检查 `git`、`make`、Python 3.11+、CMake、GCC/G++、Bison、Flex、
SWIG 和 `pkg-config`，缺失任一依赖会以非零状态退出。只有显式传入
`INSTALL_SYSTEM_DEPS=1` 时，`make setup` 才会通过 `sudo` 调用冻结 OpenROAD 的
`DependencyInstaller.sh -all`；执行前应审阅该上游脚本。`JOBS` 用于限制其依赖
构建并行度。

`make setup INSTALL_CODEX_CLI=1` 会通过 npm 安装或更新 `@openai/codex`，不会
修改 `config/credentials/`，也不会创建、读取或配置 API key。若 npm 已安装但不在
`PATH` 中，可设置 `CODEX_NPM_BIN=/path/to/npm`。

`make check` 优先使用 `.venv/bin/python`，否则使用 `python3`；它运行只读主机检查
和 AE-1，并报告 Codex CLI 是否可用于 AE-3；它不会 build OpenROAD。安装 CLI 后，
仍需单独配置 provider credential；项目不会自动创建、读取或配置 API key。

手动方式：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip pytest
PYTHONPATH=. .venv/bin/python -m artifact_evaluation.runner ae1
```

## AE-3：新鲜进化

从 [config/credentials/goalevolve_codex.env.example](config/credentials/goalevolve_codex.env.example) 建立被忽略的 `config/credentials/goalevolve_codex.env`，并执行 `chmod 600 config/credentials/goalevolve_codex.env`。GoalEvolve 永远不读取 `~/.codex`，而是从这个项目文件创建隔离的 Teacher/Student home。所有 design 共用的模型和推理强度位于 `config/codex.json`，当前为 `gpt-5.6-terra` 与 `xhigh`。

## Design Profile

项目现已携带全部八个 contest design 的输入：`aes_cipher_top`、`ariane`、`jpeg_encoder`、`mempool_group`、`nvdla_a`、`nvdla_c`、`nvdla_m`、`nvdla_p`。每个 design 的 `.def(.gz)`、Verilog、SDC 与官方初始 metrics 位于 `third_party/benchmarks/benchmarks/`；它们共享 `artifact_evaluation/lineage/openroad_power/p0/` 的 source-only OpenROAD p0 快照，并以全树内容 hash 标识。

所有 design 都使用相同的两份 profile：`experiments/<design>/baseline.json`
测量 p0，`experiments/<design>/evolve.json` 启动多轮进化。新 design 在运行前需要创建：

```text
third_party/benchmarks/benchmarks/my_design/
├── my_design.def 或 my_design.def.gz  # 放置后的起始 design
├── my_design.v                        # Verilog netlist
├── my_design.sdc                      # 时序约束
└── metrics.csv                        # 必需的 benchmark metadata
experiments/my_design/
├── baseline.json                      # 测量 p0，生成 baseline evidence
└── evolve.json                        # 固化 baseline、target 和 campaign gate
```

技术文件已经由 `third_party/benchmarks/asap7/` 共享，不能为每个 design 重复复制。

从模板建立两个 profile：

```bash
mkdir -p experiments/my_design
cp config/templates/design.baseline.example.json \
  experiments/my_design/baseline.json
cp config/templates/design.evolve.example.json \
  experiments/my_design/evolve.json
```

将两个文件中的所有 `replace_design` 改为实际 design 名称。baseline 前需要配置：

| 字段 | `baseline.json` | `evolve.json` | baseline 前填写方式 |
| --- | --- | --- | --- |
| `design` | 必填 | 必填 | benchmark 目录/设计的精确名称，例如 `my_design` |
| `source_root` | 可选 | 可选 | `null` 即共享 p0；若覆盖，两个文件必须填写相同路径 |
| `state_root` | 模板已填写 | 省略 | `outputs/baseline/my_design`；evolve 默认 `outputs/ae3/my_design` |
| `baseline_evaluation_root` | 省略 | 模板已填写 | `../../outputs/baseline/my_design` |
| `baseline_metrics` | 可保留 placeholder | 测量后替换 | 三项决策指标名必须与 target 完全相同 |
| `target_metrics` | 可保留 placeholder | 测量后替换 | 手工设置的绝对 QoR 阈值 |
| `campaign_ready` | `false` | `false` | 审阅后只将 evolve 改为 `true` |

`source_root` 省略或为 `null` 时，两个 profile 均使用共享 p0 OpenROAD 源码
`artifact_evaluation/lineage/openroad_power/p0/source`。用户也可在终端设置
`GOALEVOLVE_OPENROAD_SEED=/absolute/path/to/openroad/source`，无需改 profile。
若只想为一个 design 指定起点，将两个 profile 的 `null` 都换为同一个相对或绝对
OpenROAD 源码根目录，且该目录必须包含 `CMakeLists.txt`：

```json
"source_root": "../../artifact_evaluation/lineage/my_openroad/source"
```

保持 `campaign_ready: false`，baseline profile 中的 placeholder 指标可暂时保留，然后运行：

```bash
PYTHONPATH=. python3 -m goalevolve.cli baseline \
  --config experiments/my_design/baseline.json
```

该命令在 `outputs/baseline/my_design/baseline.json` 产生实测结果，不依赖外部
OpenROAD checkout 或预编译 binary。将其中的 `tns_abs_ns`、`dynamic_power_pw`、
`leakage_power_pw` 写入 `experiments/my_design/evolve.json` 的
`baseline_metrics`；再手工设定绝对 `target_metrics`，最后将 `campaign_ready`
改为 `true`：

```json
{
  "design": "my_design",
  "campaign_ready": true,
  "baseline_evaluation_root": "../../outputs/baseline/my_design",
  "baseline_metrics": {
    "tns_abs_ns": 100.0,
    "dynamic_power_pw": 300000000000.0,
    "leakage_power_pw": 100000000.0
  },
  "target_metrics": {
    "tns_abs_ns": 80.0,
    "dynamic_power_pw": 270000000000.0,
    "leakage_power_pw": 80000000.0
  }
}
```

上面的数值仅为示例。两个 map 的指标名必须完全一致；target 是手工设定的绝对 QoR
门槛，不能填写比例，也不能混用不同 OpenROAD 源码、不同 flow 阶段或 pre-route 的
结果。`run` 会从 `evolve.json` 读取冻结的 baseline/target，并在创建 Student
workspace 前验证 `baseline_evaluation_root` 的 evidence。完成审阅后启动：

```bash
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/my_design/evolve.json --rounds 10
```

已审核目标的 AES 可直接启动：

```bash
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/aes_cipher_top/evolve.json --rounds 10
```

所有 `evolve.json` 都不显式设置 `state_root`，因此自动写入
`outputs/ae3/<design>/`。同一命令再次执行会从最后一个已完成 round 续跑，并新增
指定的 `--rounds` 数量。AES、JPEG 已可启动；其他 design 在完成自己的 p0 baseline
前由 `campaign_ready: false` 阻止启动。完整流程见 [experiments/README.md](experiments/README.md)。新鲜 campaign 只写入 `outputs/`；不得要求它精确复现 R54 数值。

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
