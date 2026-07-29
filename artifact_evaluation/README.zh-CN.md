# Artifact evaluation

本目录把确定性的 release 结果验证与随机性的 LLM 进化实验分开。

## AE-1：环境与接口预检

```bash
PYTHONPATH=. python3 -m artifact_evaluation.runner ae1
```

AE-1 检查 release manifest、冻结 AES 源码、可移植 Tcl、官方
parser/checker、全部八个 design 的 `.def(.gz)`/Verilog/SDC/metrics、ASAP7
文件、共享 OpenROAD p0 的 manifest/content digest、Python 与 CMake。PATH 中的
`openroad` 会被记录，但不是必需条件，因为 AE-2 可自行 build 冻结源码。JSON 输出是权威预检记录。

## AE-2：固定 artifact 的确定性复验

```bash
make build-tools JOBS=8
PYTHONPATH=. python3 -m artifact_evaluation.runner ae2 \
  --artifact aes_r54_student1 --rebuild --jobs 8 --verbose
```

该命令绝不启动 Teacher、Student、retrieval、Codex 或 API 调用。它 build 不可变的 `lineage/aes_cipher_top/r054_student1/source`，在 `outputs/ae2/` 执行路径归一化后的 R54 Tcl，解析指标，运行官方 4/4 checker，并按 `release_manifest.json` 的容差对比三项决策指标。

执行完整重建前，可运行 `make ae2-preflight`。该命令检查 `make build-tools`
生成的本机 Boost 1.87 前缀，并执行与 AE-2 完全相同的 CMake 配置，但不编译
OpenROAD。完整 AE-2 带 `--verbose` 时会将编译和 flow 日志持续输出到终端，日志
仍会保存在 `outputs/ae2/`。

固定结果的阶段是 post-route `global_route + estimate_parasitics`，不是 detailed routing。AE-2 通过证明发布的源码 artifact、benchmark、checker 和 flow 可以产生报告结果；它不证明新鲜 LLM 进化一定会再次找到同一个 patch。

## AE-3：新鲜进化

从 `config/credentials/goalevolve_codex.env.example` 建立权限为 `0600` 的 `config/credentials/goalevolve_codex.env`；它被 Git 忽略，绝不能提交：

```dotenv
GOALEVOLVE_OPENAI_API_KEY=<你的独立 key>
GOALEVOLVE_PROVIDER_NAME=OpenAI
GOALEVOLVE_BASE_URL=https://api.openai.com/v1
GOALEVOLVE_WIRE_API=responses
GOALEVOLVE_REQUIRES_AUTH=true
GOALEVOLVE_PREFERRED_AUTH_METHOD=apikey
GOALEVOLVE_DISABLE_RESPONSE_STORAGE=true
GOALEVOLVE_NETWORK_ACCESS=true
GOALEVOLVE_GOALS=true
GOALEVOLVE_WSL_ACK=true
```

该文件是唯一凭据来源。worker home 在 `outputs/` 中生成，绝不继承 `~/.codex`。所有 design 共用的 `config/codex.json` 将 Teacher 和 Student 设为 `gpt-5.6-terra`、`xhigh`。

```bash
# 正常四个独立 Student 的新鲜 campaign。
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/aes_cipher_top/evolve.json --rounds 10
```

AE-3 通过指 key、Codex 调用、源码编辑、build、flow、官方检查、evidence 和 campaign 记录均完成。它必须报告 token 与 verified QoR，但不能要求某个固定 QoR，因为模型输出和搜索调度均有随机性。

## 证据清单

`expected/aes_cipher_top/r054_student1/` 保存 candidate/evidence/hypothesis、metrics、observer score、原始和可移植 Tcl、source diff 与 source manifest。`lineage/` 保存完整不可变源码；新生成的 ODB、build、flow 和 API session 必须写在 `outputs/`，不属于 release evidence。

重组前后的包级审计可运行：
`python3 artifact_evaluation/audit_migration.py --reference ../GoalEvolve_v2 --format markdown`。
模块映射和有意保留的可移植性差异见 `artifact_evaluation/MIGRATION_AUDIT.md`。

## 本地验证快照

以下生成证据产生于 2026-07-27，并保留在被忽略的 `outputs/` 下：

| 类型 | 证据路径 | 结果 |
|---|---|---|
| AE-1 | 命令 JSON stdout | passed；所有发布路径和 Python/CMake 接口存在 |
| AE-2 | `outputs/ae2/aes_r54_student1/report/ae2_report.json` | passed；使用重建的 `build/bin/openroad`，TNS `15.79 ns`、dynamic `335.9714B pW`、leakage `28.6M pW`、官方 4/4 pass |
| AE-3 | 本地历史 smoke 记录 | 完成一轮真实 Teacher/Student；候选激活并通过 4/4，随后因 QoR 未改善被 refute |

该历史 smoke 的 total tokens 为 `1,682,734`。生成的 API home 和 `auth.json` 文件已被忽略，绝不能复制到 release evidence 中。
