# GoalEvolve: From Handcrafted Algorithm Priors to Goal-Driven Evolution of Physical Design Algorithms

An open-source goal-driven framework for evolving bounded OpenROAD C++ algorithms toward specified QoR targets. It integrates post-route QoR evaluation and validity checks into a traceable source-level search workflow.

The accompanying paper is [GoalEvolve.pdf](paper/GoalEvolve.pdf).

A demonstration video is available at [Demo Video](images/video.mp4).

<p align="center">
  <img src="images/goalevolve_overview_v3.png" alt="GoalEvolve overview: frozen QoR targets guide checkpoint diagnosis, bounded OpenROAD source evolution, full-flow evaluation, and evidence-based promotion." width="100%">
</p>

```text
Frozen QoR contract
  -> bottleneck diagnosis and mechanism-card retrieval
  -> Teacher planning and Student task distribution
  -> bounded C++ editing and isolated OpenROAD builds
  -> post-placement optimization and global routing
  -> final QoR parsing and validity checks
  -> EPD evidence recording and improvement promotion
```

## Code Structure

```text
GoalEvolve/
├── Makefile                    # Environment doctor, setup, and AE-1 check entry points
├── scripts/human/              # Implementations for Makefile environment commands
├── goalevolve/                 # GoalEvolve Python implementation and CLI
│   ├── agents/                  # Teacher/Student Codex workers
│   ├── planning/                # Diagnosis, retrieval, and evidence memory
│   ├── execution/               # Isolated workspaces and campaign engine
│   ├── evaluation/              # Tcl generation, QoR parsing, and 4/4 checks
│   ├── dashboard.py             # Read-only local AE-3 dashboard server
│   └── dashboard_static/        # Browser interface for persisted campaign state
├── artifact_evaluation/         # Deterministic artifact-evaluation entry points
│   ├── lineage/                 # Immutable OpenROAD source snapshots
│   └── expected/                # Fixed QoR/evidence manifests and portable Tcl
├── experiments/                 # Reviewed design profiles and campaign examples
├── config/                      # Schema, global Codex policy, templates, and credential example
├── third_party/                 # reference inputs and official checker
├── toolchain/                   # Release toolchain lock
├── paper/                       # Paper PDF
├── tests/                       # Unit, integration, and artifact tests
└── outputs/                     # Ignored builds, flows, Codex sessions, and campaigns
```

`goalevolve/cli.py` is the public command implementation. `artifact_evaluation/lineage/` is immutable input to fixed replay; fresh Students never edit it in place. All generated content belongs under `outputs/`.

## Dependencies

- [Python](https://www.python.org/) 3.12 (validated with 3.12.13; 3.11 or newer supported)

  - `make setup` creates the project-local Conda environment from
    [`toolchain/environment.yml`](toolchain/environment.yml).

- [GCC/G++](https://gcc.gnu.org/) 13.3.1 (9 or newer supported)

  - Required to build OpenROAD source candidates. The compiler must match the
    prepared OpenROAD workspace.

- [CMake](https://cmake.org/) 3.31.9

  - Use the same CMake installation that configured the prepared OpenROAD
    workspace.

- [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD) p0 source snapshot

  - The shared source originates from revision
    `d231bd8f98d2a0adb8369002b2c1e7aa8e7877ed` and is identified by the
    shipped content digest. Use ORFS revision
    `bf8884df3b33d77d4f9cf60ed90f9d57de8997a4` with the standard OpenROAD
    build stack: Bison 3.8.2, Flex 2.6.4, SWIG 4.3.0, Boost 1.89.0, Eigen
    3.4, spdlog 1.15.0, Tcl/Tk, zlib, and libffi. Tcl/Tk, zlib, and libffi
    are supplied by the host distribution. Prepare these components with the snapshot's
    [`DependencyInstaller.sh`](artifact_evaluation/lineage/openroad_power/p0/source/etc/DependencyInstaller.sh)
    or a compatible OpenROAD/ORFS installation.

- [Codex CLI](https://www.npmjs.com/package/@openai/codex) 0.146.0 (validated)

  - Requires Node.js 16 or newer. Install it into project-generated state with
    `make setup INSTALL_CODEX_CLI=1`.

## Reproduction tracks

GoalEvolve separates a deterministic artifact claim from a fresh LLM-driven experiment.

| Track | Purpose | Network/API key | Expected result |
|---|---|---:|---|
| AE-1 | Check that the release, benchmark, checker, and host interfaces are present | No | Deterministic pass/fail |
| AE-2 | Replay the frozen AES R54 Student 1 artifact | No | Deterministic within manifest tolerances |
| AE-3 | Launch a new Teacher/Student source-evolution campaign | Yes | Workflow completion; QoR is stochastic |

Use AE-1 and AE-2 to reproduce the released artifact. With a Codex-capable environment and an independent API credential, use AE-3 to evolve a design toward predefined QoR goals.

## Environment

### Install the environment

Use a Linux host. `make setup` bootstraps a project-local Python environment
and optionally the Codex CLI; it never uses `sudo` or writes system packages.
Prepare OpenROAD/ORFS separately, using its own supported build instructions.
AE-2 and AE-3 inherit that active environment: OpenROAD, compiler, CMake, and
all linked libraries must come from one compatible workspace.

### Platform support

Linux `x86_64` is the validated platform for AE-1, AE-2, and AE-3. Linux ARM64
can run the Python setup and AE-1, but its OpenROAD build, post-route flow,
official checks, and AE-3 campaign results are unvalidated. An ARM64 failure or
QoR difference is therefore unsupported rather than an artifact regression.

```bash
git clone https://github.com/CODA-Team/GoalEvolve.git
cd GoalEvolve

# Inspect the host and immutable release inputs.
make doctor

# Create the project-local Python environment.
make setup

# Install or update the Codex CLI for AE-3. This does not configure an API key.
make setup INSTALL_CODEX_CLI=1

# Run the project check and AE-1 preflight.
make check
```

Before AE-2 or AE-3, activate a prepared OpenROAD/ORFS workspace and export
its matching executable. Do not mix a binary from one build with libraries or
source from another. `make doctor` reports whether the selected executable can
start in the current shell.

```bash
# Example only: use the activation command documented by your OpenROAD/ORFS workspace.
source /path/to/prepared/openroad-environment.sh
export OPENROAD_EXE=/path/to/prepared/OpenROAD/build/bin/openroad
make doctor
```

`make setup INSTALL_CODEX_CLI=1` installs or updates the `@openai/codex` npm
package inside `outputs/toolchain/codex-cli`. It changes neither
`config/credentials/` nor any API-key setting.

`make check` loads `outputs/toolchain/activate.sh`, runs the environment doctor
and AE-1, and reports Codex CLI availability. It does not install or build
OpenROAD.

For AE-3, configure a provider credential after installing the CLI. The project
does not create, read, or configure API keys automatically.

AES replay runs a complete physical-design flow. Disk, memory, and wall time
are design- and host-dependent. Start with a conservative OpenROAD thread/job
count; do not assume that `nproc` is appropriate on a shared machine. To place
the generated Python environment elsewhere, set `GOALEVOLVE_CONDA_HOME`,
`GOALEVOLVE_CONDA_PREFIX`, and `GOALEVOLVE_CONDA_PACKAGES_DIR` before
`make setup`.

## Inputs and data boundaries

The release includes the material required for the fixed AES artifact:

- `third_party/benchmarks/`: AES benchmark inputs and ASAP7 technology files used by AE-2.
- `third_party/official_checker/`: the official log parser and 4/4 validity checker snapshot.
- `artifact_evaluation/lineage/aes_cipher_top/r054_student1/source/`: the frozen OpenROAD source for the fixed replay.

The repository also carries AE-3 profiles for `aes_cipher_top`, `ariane`, `jpeg_encoder`, `mempool_group`, `nvdla_a`, `nvdla_c`, `nvdla_m`, and `nvdla_p`. Availability of a profile does not replace any license or access requirement for benchmark data on a separate deployment. See [experiments/README.md](experiments/README.md) before starting a new design.

## Quick start: fixed artifact

Run the release preflight first:

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae1
```

AE-1 verifies the release manifest, frozen source, AES benchmark, ASAP7 files,
official parser/checker, and Python. It does not require a working OpenROAD
installation.

Then replay the frozen AES artifact:

```bash
export OPENROAD_EXE=/path/to/prepared/OpenROAD/bin/openroad
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2-preflight \
  --artifact aes_r54_student1 --openroad "$OPENROAD_EXE" --verbose
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2 \
  --artifact aes_r54_student1 --openroad "$OPENROAD_EXE" --verbose
```

`ae2-preflight` verifies that the prepared executable starts with the active
host toolchain. Use `--rebuild` only when that host toolchain is already known
to build the frozen source revision.
For the full replay, `--verbose` streams OpenROAD flow output to the terminal
while preserving logs under `outputs/ae2/`. A successful AE-2 report proves
that the prepared OpenROAD executable ran the captured flow and passed the
official validity check. `--rebuild` is available only when the active host
toolchain is already known to build the frozen source revision.

AE-2 performs the following steps:

1. Runs the captured, path-portable `evaluate.tcl` from the original placed AES inputs.
2. Executes repair, legalizes placement, performs global routing, and estimates routing parasitics.
3. Parses post-route TNS, dynamic power, and leakage power.
4. Runs the official reference 4/4 validity checker.
5. Compares the observed metrics to [the frozen manifest](artifact_evaluation/release_manifest.json) within the declared tolerances.

The fixed claim is the `global_route + estimate_parasitics` endpoint, not detailed routing. Review [artifact_evaluation/README.md](artifact_evaluation/README.md) for the exact replay contract and report locations.

## Fixed AES result

The released artifact `aes_r54_student1` corresponds to `round_054:student_1`. Its expected post-route evidence is:

| Metric | Expected value |
|---|---:|
| TNS | `15.79 ns` |
| Dynamic power | `335.9714B pW` |
| Leakage power | `28.6M pW` |
| DRV | `0` |
| Official validity | `4/4 pass` |

SPPA and Sfinal are retained as observer-only reports. They never participate in retrieval, source selection, or promotion. AE-2's authoritative output is `outputs/ae2/aes_r54_student1/report/ae2_report.json`; its flow log, metrics, Tcl, and official-check log are under `outputs/ae2/aes_r54_student1/contest_output/`.

## Fresh evolution (AE-3)

AE-3 uses Codex Teacher and Student workers to create new C++ edits. It is not deterministic and should not be expected to rediscover the released R54 patch.

Create the project-local credential file from the committed template:

```bash
cd /path/to/GoalEvolve
cp config/credentials/goalevolve_codex.env.example \
  config/credentials/goalevolve_codex.env
chmod 600 config/credentials/goalevolve_codex.env
```

Set the provider fields and API key in `config/credentials/goalevolve_codex.env`. Do not commit this file. GoalEvolve reads this project-local dotenv file, creates isolated Teacher/Student homes under `outputs/`, and does not inherit credentials from `~/.codex`.

The shared Teacher/Student model, reasoning effort, retry, and timeout policy is versioned in [`config/codex.json`](config/codex.json). It applies to every design; API keys remain only in the ignored credential file.

Verify that the Codex client is available, then start a normal multi-round AES
campaign:

```bash
cd /path/to/GoalEvolve
source outputs/toolchain/activate.sh
codex --version
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli run \
  --config experiments/aes_cipher_top/evolve.json --rounds 10
```

The profile controls source scope, build jobs, per-command timeout, absolute objective targets, and campaign state path. Model and reasoning policy is shared by all designs through `config/codex.json`. `state_root` is optional; when omitted, a campaign writes to `outputs/ae3/<design>/`. The supplied global policy uses `gpt-5.6-terra` with `xhigh` reasoning; this setting is not a claim that another model/provider will behave equivalently.

`run` is the only public command that starts a Teacher/Student evolution campaign. `baseline` measures a fixed p0 before preparing a new profile; `official-check` validates an existing post-flow result; `sfinal-observe` and `leaderboard` generate observer-only reports; `import-legacy` imports explicit metadata; and `smoke` is a mock-only test helper.

## Starting a different design

Every design follows the same two-profile process. Create these project files
before running a new design:

```text
third_party/benchmarks/benchmarks/my_design/
├── my_design.def or my_design.def.gz   # Placed starting design
├── my_design.v                         # Verilog netlist
├── my_design.sdc                       # Timing constraints
└── metrics.csv                         # Required benchmark metadata
experiments/my_design/
├── baseline.json                       # Measures p0 and writes baseline evidence
└── evolve.json                         # Holds frozen baseline, targets, and campaign gate
```

The required technology data is already shared at
`third_party/benchmarks/asap7/`; do not copy it into each design directory.

Create the two profiles from the committed templates:

```bash
mkdir -p experiments/my_design
cp config/templates/design.baseline.example.json \
  experiments/my_design/baseline.json
cp config/templates/design.evolve.example.json \
  experiments/my_design/evolve.json
```

In both files, replace every `replace_design` with `my_design`. Configure the
following fields before the baseline command:

| Field | `baseline.json` | `evolve.json` | Value before baseline |
| --- | --- | --- | --- |
| `design` | required | required | Exact benchmark directory/name, for example `my_design` |
| `source_root` | optional | optional | `null` means the shared p0 source; for a host-backed AE-3 build, use the same matching OpenROAD source workspace in both files |
| `build_seed_root` | optional | optional | `null` starts a fresh build; set this to the same workspace when it contains a matching `build/` or `build_power/` cache |
| `state_root` | required by template | omit | `outputs/baseline/my_design`; evolve defaults to `outputs/ae3/my_design` |
| `baseline_evaluation_root` | omit | required by template | `../../outputs/baseline/my_design` |
| `baseline_metrics` | placeholder permitted | replace after measurement | Three decision-metric names must match target names |
| `target_metrics` | placeholder permitted | replace after measurement | Manual, absolute QoR thresholds |
| `campaign_ready` | `false` | `false` | Change only evolve to `true` after review |

When `source_root` is omitted or `null`, both profiles use the shared p0
OpenROAD source at `artifact_evaluation/lineage/openroad_power/p0/source`. A
user can change that default without editing a profile by exporting
`GOALEVOLVE_OPENROAD_SEED=/absolute/path/to/openroad/source`. For an actual
AE-3 campaign, point it to the source checkout that matches the activated host
toolchain. To select a source for one design only, replace `null` in both
profiles with the same relative or absolute source root; it must contain
`CMakeLists.txt`. Set `build_seed_root` to that workspace as well when it has
a matching `build/` or `build_power/` directory, so candidate builds can use a
relocated copy-on-write cache:

```json
"source_root": "/path/to/prepared/OpenROAD",
"build_seed_root": "/path/to/prepared/OpenROAD"
```

Leave `campaign_ready` as `false` and leave the placeholder metric values in
place for the baseline command. Then measure p0:

```bash
# First activate the matching OpenROAD/ORFS environment in this shell.
source /path/to/prepared/openroad-environment.sh
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli baseline \
  --config experiments/my_design/baseline.json
```

This writes `outputs/baseline/my_design/baseline.json`. Copy the measured
`tns_abs_ns`, `dynamic_power_pw`, and `leakage_power_pw` into
`experiments/my_design/evolve.json` `baseline_metrics`, choose absolute
`target_metrics`, and set `campaign_ready` to `true`:

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

The values above are examples only. The baseline and target maps must contain
the same metric names; targets are manual, absolute QoR limits, never ratios.
Do not combine pre-route values, measurements from a different OpenROAD source,
or another Tcl schedule with a full-flow campaign contract. `run` reads the
frozen metric maps from `evolve.json` and verifies the evidence at
`baseline_evaluation_root` before it creates a Student workspace. Start the
campaign after the review:

```bash
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli run \
  --config experiments/my_design/evolve.json --rounds 10
```

AES and JPEG have reviewed targets and are launchable. The other supplied
designs intentionally stop at the baseline step until their own measured p0
record is frozen. Every `evolve.json` omits `state_root`, so it automatically
writes to `outputs/ae3/<design>/`; repeating a `run --rounds N` command resumes
there and appends rounds. See [experiments/README.md](experiments/README.md)
for the complete uniform workflow.

## Outputs and result inspection

GoalEvolve keeps generated state out of version control:

```text
outputs/
  ae2/<artifact>/
    contest_output/                Tcl, log, DEF/ODB, metrics, 4/4 report
    report/ae2_report.json         executable, preflight, and artifact comparison
  ae3/<campaign>/
    rounds/round_XXX/
      diagnosis.json               dominant target gap and checkpoint evidence
      students/<student>/artifacts/
        candidate.json             QoR, checks, source provenance
        evidence.json              mechanism attribution and verdict
        contest_output/            flow outputs, metrics, 4/4 report
    parent.json                    promoted execution champion
```

A result is eligible for promotion only after its build, flow, metrics, placement, and official 4/4 checks pass. Checkpoint records distinguish immediate power/timing effects from the post-route result. A candidate that improves a local checkpoint but fails the final contract is retained as negative mechanism evidence rather than promoted.

## Testing and validation

Run the in-tree test suite:

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m pytest
```

For a reproducibility claim, report all three of the following separately:

1. AE-1 environment/interface preflight.
2. AE-2 fixed-source replay, including the manifest comparison and official 4/4 result.
3. AE-3 workflow status, model/provider configuration, token usage, and verified QoR for every valid full-flow candidate.

AE-3 workflow completion is not evidence of a fixed QoR result. Conversely, a successful AE-2 replay validates the released artifact, not the stochastic rediscovery of its source edit.

## Further documentation

- [Artifact evaluation](artifact_evaluation/README.md): AE-1, AE-2, AE-3 semantics and evidence inventory.
- [Toolchain lock](toolchain/README.md): version-lock policy and artifact boundary.
- [Implementation map](goalevolve/README.md): ownership of planning, execution, evaluation, and agents.
- [Configuration guide](config/README.md): profiles, path resolution, and credentials policy.
- `PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli --help`: public command reference. `run` is the only command that performs fresh source evolution; the other commands measure, validate, import, or report on artifacts.
- [Paper](paper/GoalEvolve.pdf): framework, experimental setup, goal-attainment results, and AES case study.

## Web Demo

The local Web Demo visualizes one persisted AE-3 campaign. It refreshes the Teacher's recorded ideas, Student execution states, QoR trajectory, frozen targets, and the Top-3 verified QoR results; it displays only saved campaign artifacts and never exposes hidden model reasoning.

Start it after a campaign has initialized its `outputs/ae3/<design>/` state root:

```bash
cd /path/to/GoalEvolve
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli dashboard \
  --state-root outputs/ae3/aes_cipher_top --port 8080
```

Open `http://127.0.0.1:8080`. The dashboard is read-only and does not launch, alter, or stop an AE-3 campaign. For a remote evaluation server, create a local tunnel and open the same address in the local browser:

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
