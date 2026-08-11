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

## P0 源码图与 Doc Card

项目内 P0 快照已经预构建 `src/rsz` 与 `src/rmp` 的 tree-sitter 源码图，位置为
`artifact_evaluation/lineage/openroad_power/p0/repository_graph/`。其中
`manifest.json`、`graph.json`、`doc_cards.json` 以 P0 内容 hash 记录文件、符号、
include/call 关系及可恢复的 parser 错误。图只索引生产 C++ 实现与头文件，不包含
模块 `test/tests` 目录。每轮 Codex Teacher 以这一 P0 图为基线，为不可变 campaign parent 在
`<state_root>/knowledge/repository_graph/<source_hash>/` 建立增量图；未变化文件复用
P0 事实，变化文件才重新解析。

Teacher 只接收当前 design 允许 patch roots 内的受限 AST 诱导子图和 Doc Card，图中仅保留
两端都在该包内的关系，仍必须用 `rg`/`sed` 检查 live parent。Controller 会同时校验 `path::symbol` anchor 的 AST 唯一性与文件
digest。函数重载时必须使用 Doc Card 的完整 declarator，例如
`src/rsz/src/RecoverPower.cc::rsz::RecoverPower::recoverPower(const float recover_power_percent, bool verbose)`；
Teacher Markdown 的多个 `Source Evidence` anchor 使用分号分隔，因此 C++ 参数列表中的逗号不会被拆开。
`round_NNN/search_policy.json` 仅汇总 EPD 和已完成轮次的建议。连续两轮保留 parent 后，它会记录真实尝试过的
hook 和建议的 `avoid_exact_source_hooks` 前沿，要求 Teacher 换用新 hook 或说明实质不同的决策边界；它不能选择 parent、
修改 Student 角色、拒绝一个可验证机制或绕过正式 promotion gate。

## 三类 artifact evaluation

| 类型 | 证明内容 | 是否使用 API key | 是否有稳定 pass/fail |
|---|---|---:|---:|
| AE-1 | 源码、benchmark、checker、manifest 与本机接口完整 | 否 | 是 |
| AE-2 | 固定 AES R54 Student 1 artifact 的 post-route replay | 否 | 是，使用明确容差 |
| AE-3 | 用户能启动新的 Teacher/Student 源码进化 campaign | 是 | 只检查流程；QoR 本身随机 |

立即运行 AE-1：

```bash
cd /path/to/GoalEvolve
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae1
```

运行确定性的 AE-2：

```bash
source outputs/toolchain/activate.sh
export OPENROAD_EXE=/path/to/prepared/OpenROAD/build/bin/openroad
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2-preflight \
  --artifact aes_r58_student1 --openroad "$OPENROAD_EXE"
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2 \
  --artifact aes_r58_student1 --openroad "$OPENROAD_EXE"
```

AE-2 使用版本匹配、且在当前 shell 已激活的 OpenROAD 执行可移植的已捕获 Tcl、官方 parser 与 4/4 checker，然后与 `artifact_evaluation/expected/aes_cipher_top/r058_student1/metrics.json` 对比 TNS、dynamic power、leakage。该结果的阶段是 `global_route + estimate_parasitics`，不是 detailed routing。

## 环境安装

使用 Linux 主机。固定源码和工具链 provenance 保存在
[`toolchain/lock.json`](toolchain/lock.json)。`make setup` 只负责项目内 Python
环境和可选的 Codex CLI：没有 Conda 时会自举 Miniforge，不会使用 `sudo`，也不会写入
`/usr`、`/opt` 或系统包管理器。OpenROAD/ORFS 必须由用户按该工作区自身的说明准备，
并在 AE-2 或 AE-3 前激活。OpenROAD、编译器、CMake 与全部动态库必须来自同一个兼容
workspace，不能由 GoalEvolve 在不同宿主机上临时拼装。

### 平台支持

Linux `x86_64` 是 AE-1、AE-2 和 AE-3 的已验证平台。Linux ARM64 可以运行 Python
安装和 AE-1；但 OpenROAD build、post-route flow、官方检查和 AE-3 campaign 尚未在
ARM64 上验证。因此 ARM64 上的 build/flow 失败或 QoR 差异属于未支持平台行为，不能
判定为 artifact regression。`make doctor` 会报告这一状态。

```bash
git clone https://github.com/CODA-Team/GoalEvolve.git
cd GoalEvolve

# 检查宿主机和冻结输入。
make doctor

# 创建项目内 Python 环境。
make setup

# 为 AE-3 安装或更新 Codex CLI；该命令不会配置 API key。
make setup INSTALL_CODEX_CLI=1

# 执行主机检查和 AE-1 预检。
make check
```

准备 OpenROAD/ORFS 后，按该 workspace 自身的说明先激活环境，再指向其二进制：

```bash
# 示例：实际激活命令由你的 OpenROAD/ORFS workspace 提供。
source /path/to/prepared/openroad-environment.sh
export OPENROAD_EXE=/path/to/prepared/OpenROAD/build/bin/openroad
make doctor
```

`make doctor` 可在未准备环境的主机上运行，检查 Git、Make、Python bootstrap 与冻结
输入；设置 `OPENROAD_EXE` 后还会验证该 binary 能否在当前 shell 启动。`make check`
加载项目 Python 环境、运行 doctor 和 AE-1，不安装或编译 OpenROAD。

`make setup INSTALL_CODEX_CLI=1` 会通过 npm 安装或更新 `@openai/codex`，不会
修改 `config/credentials/`，也不会创建、读取或配置 API key；CLI 会安装在
`outputs/toolchain/codex-cli`。

`make check` 会加载 `outputs/toolchain/activate.sh`，运行 environment doctor 和 AE-1，
并报告 Codex CLI 是否可用于 AE-3；它不会 build OpenROAD。安装 CLI 后，仍需单独配置
provider credential；项目不会自动创建、读取或配置 API key。若需要在当前用户 home
中的其他目录保存生成环境，可在 `make setup` 前设置 `GOALEVOLVE_CONDA_HOME`、
`GOALEVOLVE_CONDA_PREFIX` 和 `GOALEVOLVE_CONDA_PACKAGES_DIR`。

`make setup` 成功后会生成 `outputs/toolchain/activate.sh`。AE-2 和 AE-3 所需的
OpenROAD 构建依赖、环境脚本和动态库由用户准备的 workspace 负责维护。

## AE-3：新鲜进化

从 [config/credentials/goalevolve_codex.env.example](config/credentials/goalevolve_codex.env.example) 建立被忽略的 `config/credentials/goalevolve_codex.env`，并执行 `chmod 600 config/credentials/goalevolve_codex.env`。GoalEvolve 永远不读取 `~/.codex`，而是从这个项目文件创建隔离的 Teacher/Student home。所有 design 共用的模型和推理强度位于 `config/codex.json`，当前为 `gpt-5.6-terra` 与 `xhigh`。

### EPD v2 生命周期

每个 campaign 将演化程序数据库写入
`outputs/ae3/<design>/knowledge/epd.json`。任一 Student 开始前，Teacher 的每个结构化
idea 都会保存预测阶段效果、源码 hook、预期信号、排序和 `pending` 状态。Student 完成真实
测评后，该 idea 更新为 `validated`、`promising` 或 `invalid`；只有从未执行的 idea 保持
`pending`。

Integrator 可从所有带有持久化源码 diff 的非 `invalid` 尝试中选取组合；Enhancer 仅可接收
`promising` idea；按 Teacher 优先级排序的 `pending` idea 会进入 Explorer 的候选菜单。Enhancer
prompt 必定包含上一轮 diff artifact、修改文件、增删代码、增删机制事实和 telemetry 变化。
每个 promising idea 最多有 `epd_max_reinforcement_attempts` 次 Enhancer 强化机会，默认 `2`，
在 evolve profile 中设置且对整个 campaign 生效。结果被晋升时，对应 idea 会记录继承的 parent
和 source hash。

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
| `source_root` | 可选 | 可选 | `null` 即共享 p0；实际 AE-3 build 时，两个文件必须填写同一个与宿主环境匹配的 OpenROAD 源码路径 |
| `build_seed_root` | 可选 | 可选 | `null` 时 fresh build；若该 workspace 有匹配的 `build/` 或 `build_power/` cache，则填写同一路径 |
| `state_root` | 模板已填写 | 省略 | `outputs/baseline/my_design`；evolve 默认 `outputs/ae3/my_design` |
| `baseline_evaluation_root` | 省略 | 模板已填写 | `../../outputs/baseline/my_design` |
| `baseline_metrics` | 可保留 placeholder | 测量后替换 | 三项决策指标名必须与 target 完全相同 |
| `target_metrics` | 可保留 placeholder | 测量后替换 | 手工设置的绝对 QoR 阈值 |
| `campaign_ready` | `false` | `false` | 审阅后只将 evolve 改为 `true` |

`source_root` 省略或为 `null` 时，两个 profile 均使用共享 p0 OpenROAD 源码
`artifact_evaluation/lineage/openroad_power/p0/source`。对于 AE-3 实际 build，应将它
设为与已激活 OpenROAD/ORFS 环境匹配的源码工作区；用户也可在终端设置
`GOALEVOLVE_OPENROAD_SEED=/absolute/path/to/openroad/source`，无需改 profile。
若只想为一个 design 指定起点，将两个 profile 的 `null` 都换为同一个相对或绝对
OpenROAD 源码根目录，且该目录必须包含 `CMakeLists.txt`。该 workspace 有匹配的
`build/` 或 `build_power/` cache 时，也将 `build_seed_root` 设为同一路径，以便候选
使用隔离、重定位后的 copy-on-write cache：

```json
"source_root": "/path/to/prepared/OpenROAD",
"build_seed_root": "/path/to/prepared/OpenROAD"
```

保持 `campaign_ready: false`，baseline profile 中的 placeholder 指标可暂时保留，然后运行：

```bash
# 先按宿主 OpenROAD/ORFS workspace 的说明激活环境。
source /path/to/prepared/openroad-environment.sh
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli baseline \
  --config experiments/my_design/baseline.json
```

该命令在 `outputs/baseline/my_design/baseline.json` 产生实测结果，需要已激活且与
`source_root` 匹配的 OpenROAD/ORFS build 环境。将其中的 `tns_abs_ns`、`dynamic_power_pw`、
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
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli run \
  --config experiments/my_design/evolve.json --rounds 10
```

已审核目标的 AES 可直接启动：

```bash
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli run \
  --config experiments/aes_cipher_top/evolve.json --rounds 10
```

所有 `evolve.json` 都不显式设置 `state_root`，因此自动写入
`outputs/ae3/<design>/`。同一命令再次执行会从最后一个已完成 round 续跑，并新增
指定的 `--rounds` 数量。AES、JPEG 已可启动；其他 design 在完成自己的 p0 baseline
前由 `campaign_ready: false` 阻止启动。完整流程见 [experiments/README.md](experiments/README.md)。新鲜 campaign 只写入 `outputs/`；不得要求它精确复现固定 artifact 数值。

## 当前固定 AES artifact

`aes_r58_student1` 是 `round_058:student_1`；其冻结记录的 TNS 为
`15.68 ns`、dynamic power 为 `340.9709B pW`、leakage 为 `29.1M pW`。
复现实验应使用本文档的 AE-2 命令，以本机兼容工具链重新生成结果。

## Further documentation

- [Artifact evaluation](artifact_evaluation/README.md)：AE-1、AE-2、AE-3 语义和证据清单。
- [Toolchain lock](toolchain/README.md)：版本锁策略和 artifact 边界。
- [Implementation map](goalevolve/README.md)：planning、execution、evaluation 和 agents 的代码归属。
- [Configuration guide](config/README.md)：profile、路径解析和 credential 策略。
- `PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli --help`：公共命令参考。
- [Paper](paper/GoalEvolve.pdf)：框架、实验设置和 AES case study。
- 复现结论前先阅读 [artifact_evaluation/README.zh-CN.md](artifact_evaluation/README.zh-CN.md)；变更版本前阅读 [toolchain/README.zh-CN.md](toolchain/README.zh-CN.md)；实现导图见 [goalevolve/README.zh-CN.md](goalevolve/README.zh-CN.md)。

## Web Demo

本地 Web Demo 以只读方式展示一个已持久化的 AE-3 campaign：每轮 Teacher 已记录的 idea、Student 执行状态、QoR 迭代曲线、冻结目标和 Top-3 已验证结果。它只读取写入 `outputs/` 的 artifact，不显示隐藏模型推理，也不会启动、修改或停止 campaign。

在 campaign 已创建 `outputs/ae3/<design>/` state root 后运行：

```bash
cd /path/to/GoalEvolve
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli dashboard \
  --state-root outputs/ae3/aes_cipher_top --port 8080
```

在浏览器打开 `http://127.0.0.1:8080`。远程服务器上运行时，可在本机建立隧道：

```bash
ssh -N -L 8080:127.0.0.1:8080 USER@SERVER
```

## Authors and Artifact Evaluation Contributor

### Paper Authors

- **Haixu Liu** — Fudan University
  ([22307130026@m.fudan.edu.cn](mailto:22307130026@m.fudan.edu.cn))
- **Lei Zhou** — Fudan University
  ([zhoulei26@m.fudan.edu.cn](mailto:zhoulei26@m.fudan.edu.cn))
- **Yuhao Ren** — Fudan University
  ([24112020153@m.fudan.edu.cn](mailto:24112020153@m.fudan.edu.cn))
- **Yumao Wu** — Fudan University
  ([yumaowu@fudan.edu.cn](mailto:yumaowu@fudan.edu.cn))
- **Zhiang Wang** — Fudan University
  ([zhiangwang@fudan.edu.cn](mailto:zhiangwang@fudan.edu.cn))

### Artifact Evaluation Contributor

- **Haixu Liu** — Fudan University
  ([22307130026@m.fudan.edu.cn](mailto:22307130026@m.fudan.edu.cn))
