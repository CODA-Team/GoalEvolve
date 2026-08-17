# GoalEvolve：从手工算法先验到目标驱动的物理设计算法进化

[中文](README.zh-CN.md) · [English](README.md)

GoalEvolve 是一个开源的目标驱动框架，用于将有边界的 OpenROAD C++ 算法进化到指定 QoR 目标。它将 post-route QoR 评估和有效性检查整合进可追踪的源码级搜索流程。

配套论文：[GoalEvolve.pdf](paper/GoalEvolve.pdf)。

演示视频：[Demo Video](images/video.mp4)。

<p align="center">
  <img src="images/goalevolve_overview_v3.png" alt="GoalEvolve overview: frozen QoR targets guide checkpoint diagnosis, bounded OpenROAD source evolution, full-flow evaluation, and evidence-based promotion." width="100%">
</p>

```text
冻结的 QoR 合同
  -> 瓶颈诊断与机制卡片检索
  -> Teacher 规划与 Student 任务分配
  -> 有边界的 C++ 编辑与隔离 OpenROAD 构建
  -> placement 后优化与全局布线
  -> QoR 解析与有效性检查
  -> EPD 证据记录与改进晋升
```

## 代码结构

```text
GoalEvolve/
├── Makefile                    # 环境 doctor、setup 和 AE-1 检查入口
├── scripts/human/              # Makefile 环境命令的实现
├── goalevolve/                 # GoalEvolve Python 实现与 CLI
│   ├── agents/                  # Teacher/Student Codex worker
│   ├── planning/                # 诊断、检索与证据记忆
│   ├── execution/               # 隔离 workspace 与 campaign 引擎
│   ├── evaluation/              # Tcl 生成、QoR 解析与 4/4 检查
│   ├── dashboard.py             # 只读的本地 AE-3 dashboard server
│   └── dashboard_static/        # 持久化 campaign 状态的浏览器界面
├── artifact_evaluation/         # 确定性的 artifact evaluation 入口
│   ├── ae1/                     # release 完整性与路径检查
│   ├── ae2/                     # 固定 OpenROAD artifact 预检与重放
│   ├── ae3/                     # 指向用户驱动的 goalevolve.cli 工作流
│   ├── ae4/                     # AES binary 跨 design transfer 重放
│   ├── expected/                # 固定 QoR/evidence manifest 与可移植 Tcl
│   └── lineage/                 # 不可变的 OpenROAD 源码快照
│       └── openroad_power/p0/    # P0 source manifest 与预构建 rsz/rmp AST 图
├── experiments/                 # 经审核的 design profile 与 campaign 示例
├── config/                      # schema、全局 Codex policy、模板和 credential 示例
├── third_party/                 # reference 输入与官方 checker
├── toolchain/                   # release 工具链锁定
├── paper/                       # 论文 PDF
├── tests/                       # unit、integration 与 artifact 检查
└── outputs/                     # 被忽略的 build、flow、Codex session 和 campaign
```

`goalevolve/cli.py` 是公开的命令实现。`artifact_evaluation/lineage/` 是固定重放的不可变输入；新的 Student 不会原地编辑它。所有生成内容都属于 `outputs/`。

## 依赖

- [Python](https://www.python.org/) 3.12（已使用 3.12.13 验证；支持 3.11 或更新版本）

  - `make setup` 根据 [`toolchain/environment.yml`](toolchain/environment.yml) 创建项目内 Conda 环境。

- [GCC/G++](https://gcc.gnu.org/) 13.3.1（支持 9 或更新版本）

  - 用于构建 OpenROAD source candidate。compiler 必须与准备好的 OpenROAD workspace 匹配。

- [CMake](https://cmake.org/) 3.31.9

  - 使用配置准备好的 OpenROAD workspace 时所用的同一 CMake 安装。

- [OpenROAD](https://github.com/The-OpenROAD-Project-staging/OpenROAD/tree/f3f70f5cb24f2b2cebb0371707e03b24ae8312d6)

  - p0（`artifact_evaluation/lineage/openroad_power/p0/source/`）向该 OpenROAD revision 加入初始的 power-aware 文件，作为进化起点。它需要 Bison 3.8.2、Flex 2.6.4、SWIG 4.3.0、Boost 1.89.0、Eigen 3.4、spdlog 1.15.0，以及宿主 Tcl/Tk、zlib 和 libffi。

- [Codex CLI](https://www.npmjs.com/package/@openai/codex) 0.146.0

  - 需要 Node.js 16 或更新版本；使用 `make setup INSTALL_CODEX_CLI=1` 将其安装到项目生成的状态目录。

## 复现轨道

| 轨道 | 用途 |
|---|---|
| AE-1 | 设置项目环境并检查 release interface。 |
| AE-2 | 重建并重放八个已发布的 evolved OpenROAD source artifact。 |
| AE-3 | 为指定 design 启动 GoalEvolve campaign，或重新运行完整源码进化。 |
| AE-4 | 使用 AES-evolved executable 评估七个非 AES contest design。 |

## AE-1：环境设置

已验证平台为 Linux `x86_64`。从仓库根目录开始：

```bash
git clone https://github.com/CODA-Team/GoalEvolve.git
cd GoalEvolve

# 检查宿主机工具、release 输入和项目内环境。
make doctor

# 创建项目内 Python 环境。
make setup

# 安装 AE-3 所需的项目内 Codex CLI。
make setup INSTALL_CODEX_CLI=1
```

`make setup` 会创建项目内环境并生成 `outputs/toolchain/activate.sh`。完成
setup 后请 source 它。如果用户已经有兼容的 ORFS/GCC workspace，应在构建
p0 前按该 workspace 提供的方式激活，例如：

```bash
source /path/to/your/orfs/activate.sh
```

激活后的 workspace 必须通过 `PATH` 和 `CMAKE_PREFIX_PATH`（或等效变量）
提供 compiler 及 native dependency。如果该 workspace 已包含完整且兼容的
依赖 bundle，则跳过下面两个 `DependencyInstaller.sh` 命令。

共享服务器上的并发 Conda 操作可能暂时锁定 libmamba metadata database。`make setup` 会自动使用 Conda classic solver 重试；不需要手动删除 cache。

复制并构建 p0 以准备匹配的 OpenROAD executable。这样不会改变冻结源码快照；dependency installer 只用 `sudo` 安装宿主 package，使用 `-local` 时下载的构建依赖位于当前用户目录。执行 `Build.sh` 前，如果已准备的 OpenROAD/ORFS workspace 提供匹配的 compiler 和 native dependency bundle，请先激活它；激活后这些工具必须通过 `PATH` 和 `CMAKE_PREFIX_PATH`（或等效变量）可见。

```bash
P0_INPUT="$PWD/artifact_evaluation/lineage/openroad_power/p0/source"
P0_BUILD="$PWD/outputs/toolchain/openroad-p0"
rm -rf "$P0_BUILD"
mkdir -p "$(dirname "$P0_BUILD")"
cp -a "$P0_INPUT" "$P0_BUILD"
cd "$P0_BUILD"
# 如果已激活的 ORFS/GCC workspace 已提供全部 native dependency，则跳过下面两个 installer。
sudo ./etc/DependencyInstaller.sh -base
./etc/DependencyInstaller.sh -common -local
# AE-1 需要 production executable；项目级验证是下面的 `make check`。
./etc/Build.sh -no-tests
cd -

export OPENROAD_EXE="$P0_BUILD/build/bin/openroad"
make check
```

`-base` 会安装宿主 package，因此需要管理员允许的 `sudo` session。如果宿主已经提供列出的 compiler 和 OpenROAD 构建依赖、但共享服务器不提供 `sudo`，只跳过 `-base`，继续运行 `-common -local`；缺失的系统 package 应由管理员安装，不要绕过权限策略。如果已激活且兼容的 OpenROAD/ORFS workspace 提供完整 native dependency bundle，可以跳过**两个** installer，直接运行 `Build.sh`。这样可以避免在 `$HOME/.local` 重复下载依赖；但宿主依赖版本仍必须与冻结 p0 源码兼容。

release replay 使用 `Build.sh -no-tests`。它构建 AE-1/AE-2/AE-3 所需的 production OpenROAD executable，同时跳过可选的 upstream C++ unit-test target。GoalEvolve 自身的 release 检查由后续的 `make check` 独立运行。

`make check` 运行 AE-1 preflight，验证 release manifest、p0 与固定 source snapshot、benchmark 输入、ASAP7 数据、官方 parser/checker 和 Python interface。也可以设置 `OPENROAD_EXE` 使用单独准备的兼容 OpenROAD 环境。

## AE-2：重放固定的 evolved OpenROAD artifact

八个 AE-2 artifact 各自包含冻结的 OpenROAD source snapshot 和记录的 Tcl schedule。AE-2 重建选定 snapshot，运行捕获的 post-route flow，并将 metrics 与官方 4/4 validity result 和固定 evidence 比较。仓库包含所需的 benchmark 输入、ASAP7 数据、checker 和冻结源码。

这是**已选择结果的重放，不是新的进化运行**：不会创建 Teacher/Student candidate，也不会改变论文中的 selection。由于选定结果是对 OpenROAD 源码的修改，所以必须从各自不可变的 C++ snapshot 编译每个 artifact，才能在干净主机上测量其捕获的 flow。

先确认 AE-1 准备的 p0 executable 能在当前环境启动，然后 AE-2 会构建所选 artifact 自己的冻结 OpenROAD source 并重放其 Tcl：

```bash
source outputs/toolchain/activate.sh
export OPENROAD_EXE="$PWD/outputs/toolchain/openroad-p0/build/bin/openroad"

PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae2/run_ae2.py preflight \
  --artifact aes_cipher_top_student_code --openroad "$OPENROAD_EXE" --verbose

# 构建并重放该 artifact 自己的不可变 OpenROAD source snapshot。
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae2/run_ae2.py replay \
  --artifact aes_cipher_top_student_code --rebuild --jobs 8 --verbose
```

`--verbose` 会将 configure、build 和 flow 日志输出到终端。每个 artifact 的 evaluation mode、数值容差和预期 evidence 都记录在 [release_manifest.json](artifact_evaluation/release_manifest.json)。所有 artifact ID、source snapshot、Tcl schedule、QoR 和距离目标的记录见 [AE2_SELECTIONS.md](artifact_evaluation/AE2_SELECTIONS.md)。将 `aes_cipher_top_student_code` 替换为列表中的其他 ID 即可重放对应 design。

`--openroad` 只被 AE2 的 `preflight` mode 接受，用于检查宿主环境能否启动 OpenROAD。正式 AE-2 replay 不会使用该外部 binary，而是从选定 source 构建或复用 `outputs/ae2/<released-artifact>/build/bin/openroad`。

## AE-3：运行新的源码进化 campaign

AE-3 运行完整的 Teacher/Student 源码进化流程。它具有非确定性，通过有效的 workflow 完成和实测 QoR 评估，而不是复现已发布的 AES patch。

### 配置 Codex 访问

创建被忽略的项目 credential 文件并填写 provider 字段和 API key。所有 design 共用 [`config/codex.json`](config/codex.json) 中的 model、reasoning、retry 和 timeout policy。

```bash
cp config/credentials/goalevolve_codex.env.example \
  config/credentials/goalevolve_codex.env
chmod 600 config/credentials/goalevolve_codex.env
source outputs/toolchain/activate.sh
codex --version
```

### 添加 design 和 profile

放置 design 输入并创建下面两个 profile。ASAP7 technology data 已共享于 `third_party/benchmarks/asap7/`。

```text
third_party/benchmarks/benchmarks/my_design/
├── my_design.def or my_design.def.gz   # 放置的输入
├── my_design.v                         # Verilog netlist
├── my_design.sdc                       # timing constraints
└── metrics.csv                         # benchmark metadata
experiments/my_design/
├── baseline.json                       # p0 measurement profile
└── evolve.json                         # multi-round evolution profile
```

```bash
mkdir -p experiments/my_design
cp config/templates/design.baseline.example.json experiments/my_design/baseline.json
cp config/templates/design.evolve.example.json experiments/my_design/evolve.json
```

将所有 `replace_design` 替换为 `my_design`。将 `source_root` 保持为 `null` 可使用仓库 p0；也可以在两个 profile 中同时设置 `source_root` 和 `build_seed_root`，指向兼容的 OpenROAD workspace。`baseline.json` 写入 `outputs/baseline/my_design/`；除非显式设置 `state_root`，否则 `evolve.json` 自动写入 `outputs/ae3/my_design/`。

### 设置 QoR 合同

运行 baseline profile，将测得的 metrics 复制到 `evolve.json` 的 `baseline_metrics`。为新 design 设置绝对 TNS、dynamic-power 和 leakage-power 限制作为 `target_metrics`，并将 `campaign_ready` 设为 `true`。两个 metric map 必须使用相同名称。

```bash
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli baseline \
  --config experiments/my_design/baseline.json
```

```json
{
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

### 开始或继续 campaign

```bash
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli run \
  --config experiments/my_design/evolve.json --rounds 10
```

再次运行同一命令会向同一 campaign 追加 rounds。已提供的 AES 和 JPEG profile 已经有经过审核的目标；其他 design 见 [experiments/README.md](experiments/README.md)。

### P0-rooted AE-3（推荐）

P0 命令会将经审核的 template 复制到新的隔离 campaign，测量并冻结 baseline，然后开始进化。`ast_graph` 是默认 evidence 路径；`openroad_cards` 是明确的 no-AST ablation。两个命令都需要上述 credential 文件和 AE-1 准备好的可用 OpenROAD build 环境。

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli p0 list

# AST repository graph campaign
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli p0 start \
  --design aes_cipher_top --run-id aes_ast_10r --output-root outputs/p0_campaigns \
  --planning-mode ast_graph --rounds 10

# 对比用的 OpenROAD-card-only ablation；使用不同的 run ID。
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli p0 start \
  --design aes_cipher_top --run-id aes_cards_10r --output-root outputs/p0_campaigns \
  --planning-mode openroad_cards --rounds 10
```

使用 `p0 status --campaign <campaign-directory>` 检查状态，使用 `p0 run --campaign <campaign-directory> --rounds N` 继续运行。生成的 P0 campaign 位于 `outputs/`，不能替代固定的 AE-2 artifact。

## AE-4：重放跨 design transfer

AE-4 使用**本地重建且通过验证的** AES AE-2 executable，在七个非 AES design 上运行。它首先验证 `outputs/ae2/aes_cipher_top_student_code/report/ae2_report.json`，然后从仓库内置 ASAP7 library 生成 evaluation-local RMP ABC Liberty。不会版本化机器绝对路径、预构建 binary hash 或生成输出。

```bash
source outputs/toolchain/activate.sh

# 克隆后需要先按上面的说明构建并通过 AES AE-2。
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py prepare
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py run --jobs 1
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py collect
```

`--jobs 1` 是共享主机的安全默认值；只有确认内存和 CPU 足够时才提高它。七个 baseline flow、日志和汇总会写入 `outputs/ae4/`。固定 schedule 及结果解释见 [artifact_evaluation/ae4/README.md](artifact_evaluation/ae4/README.md)。

## 输出与结果检查

```text
outputs/
├── toolchain/                         # 项目内 Python 与 Codex CLI
├── baseline/
│   └── <design>/                      # 测量得到的 p0 baseline evidence
├── ae2/
│   └── <released-artifact>/
│       ├── report/                    # 重建、预检和 manifest 比较
│       └── contest_output/            # flow 日志、QoR metrics 和 4/4 结果
├── ae3/
│   └── <design>/
│       ├── rounds/                    # Teacher plan 与 Student evaluation
│       ├── knowledge/                 # 持久化的进化证据
│       └── parent.json                # 当前晋升的 source parent
└── ae4/
    ├── provenance.json                # 已验证的 AES AE-2 binary provenance
    ├── <design>/                      # 生成 Tcl、flow 日志和单次运行 JSON
    ├── summary.csv / summary.json     # 七个 design 的比较行
    ├── aggregate.json                 # win count 与 aggregate improvement
    ├── report.md                      # 可读的 AE-4 报告
    └── table.tex                      # 论文用对比表
```

AE-2 查看 `report/ae2_report.json` 获取固定 artifact 比较结果，查看 `contest_output/` 获取底层 flow。AE-3 查看 design 的 `rounds/` 和 `parent.json` 获取 candidate history 与当前结果。AE-4 查看 `outputs/ae4/report.md` 和 `summary.csv` 获取七个 design 的比较，单个 design 的日志和解析结果位于 `outputs/ae4/<design>/`。

<!--
## 测试与验证

AE-1 设置完成后运行仓库内测试套件：

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m pytest
```

分别报告 AE-1 preflight、AE-2 replay/4/4 结果和 AE-3 的有效测量 candidate。成功的 AE-2 replay 验证固定 artifact；AE-3 验证新的随机进化运行。
-->

## 更多文档

- [Artifact evaluation](artifact_evaluation/README.zh-CN.md)：AE-1、AE-2 重放、AE-3 运行入口和 evidence inventory。
- [AE-4 跨 design transfer](artifact_evaluation/ae4/README.md)：transfer 环境、固定 schedule 和报告解释。
- [Toolchain lock](toolchain/README.zh-CN.md)：版本锁策略和 artifact 边界。
- [Implementation map](goalevolve/README.zh-CN.md)：planning、execution、evaluation 和 agents 的代码归属。
- [Configuration guide](config/README.zh-CN.md)：profile、路径解析和 credential policy。
- [Paper](paper/GoalEvolve.pdf)：框架、实验设置、目标达成结果和 AES case study。

<!--
## Web 演示

本地 Web Demo 展示一个已持久化的 AE-3 campaign：Teacher 记录的 idea、Student 执行状态、QoR 轨迹、冻结目标和 Top-3 已验证 QoR 结果。它只显示保存的 campaign artifact，不暴露隐藏的模型推理。

在 campaign 初始化 `outputs/ae3/<design>/` state root 后运行：

```bash
cd /path/to/GoalEvolve
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli dashboard \
  --state-root outputs/ae3/aes_cipher_top --port 8080
```

打开 `http://127.0.0.1:8080`。dashboard 是只读的，不会启动、修改或停止 AE-3 campaign。远程 evaluation server 可建立本地 tunnel，在本地浏览器打开同一地址：

```bash
ssh -N -L 8080:127.0.0.1:8080 USER@SERVER
```
-->

## 作者与 Artifact Evaluation 贡献者

### 论文作者

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

### Artifact Evaluation 贡献者

- **Haixu Liu** — Fudan University
  ([22307130026@m.fudan.edu.cn](mailto:22307130026@m.fudan.edu.cn))
