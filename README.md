# GoalEvolve: From Handcrafted Algorithm Priors to Goal-Driven Evolution of Physical Design Algorithms

English · [中文](README.zh-CN.md)

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
│   ├── ae1/                     # Release completeness and path validation
│   ├── ae2/                     # Fixed OpenROAD artifact preflight and replay
│   ├── ae3/                     # Pointer to the user-driven goalevolve.cli workflow
│   ├── ae4/                     # AES-binary cross-design transfer replay
│   ├── expected/                # Fixed QoR/evidence manifests and portable Tcl
│   └── lineage/                 # Immutable OpenROAD source snapshots
│       └── openroad_power/p0/    # P0 source manifest and prebuilt rsz/rmp AST graph
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

- [OpenROAD](https://github.com/The-OpenROAD-Project-staging/OpenROAD/tree/f3f70f5cb24f2b2cebb0371707e03b24ae8312d6)

  - p0 (`artifact_evaluation/lineage/openroad_power/p0/source/`) adds the
    initial power-aware files to this OpenROAD revision as the evolution
    starting point. It requires Bison 3.8.2, Flex 2.6.4, SWIG 4.3.0, Boost
    1.89.0, Eigen 3.4, spdlog 1.15.0, and host Tcl/Tk, zlib, and libffi.

- [Codex CLI](https://www.npmjs.com/package/@openai/codex) 0.146.0

  - Requires Node.js 16 or newer. Install it into project-generated state with
    `make setup INSTALL_CODEX_CLI=1`.

## Reproduction tracks

| Track | Purpose |
|---|---|
| AE-1 | Set up the project environment and check the release interfaces. |
| AE-2 | Rebuild and replay each of the eight released evolved OpenROAD source artifacts. |
| AE-3 | Launch a GoalEvolve campaign for a supplied design or rerun full source evolution. |
| AE-4 | Evaluate the AES-evolved executable on the seven non-AES contest designs. |

## AE-1: Environment setup

The validated platform is Linux `x86_64`. Start in the repository root:

```bash
git clone https://github.com/CODA-Team/GoalEvolve.git
cd GoalEvolve

# Inspect host tools, release inputs, and the project-local environment.
make doctor

# Create the project-local Python environment.
make setup

# Install the project-local Codex CLI, needed by AE-3.
make setup INSTALL_CODEX_CLI=1
```

`make setup` creates the project-local environment and generates
`outputs/toolchain/activate.sh`. Source it after setup. If you already have a
compatible ORFS/GCC workspace, activate it using that workspace's own command
before building p0, for example:

```bash
source /path/to/your/orfs/activate.sh
```

The activated workspace must expose its compiler and native dependencies through
`PATH` and `CMAKE_PREFIX_PATH`. When it provides the full compatible dependency
bundle, skip both `DependencyInstaller.sh` commands below.

On a shared server, a concurrent Conda operation can transiently lock
libmamba's metadata database. `make setup` automatically retries with Conda's
classic solver in that case; no manual cache deletion is needed.

Copy and build p0 to prepare the matching OpenROAD executable. This keeps the
frozen source snapshot unchanged; the dependency installer uses `sudo` only for
host packages, while `-local` keeps downloaded build dependencies under the
current user. Before `Build.sh`, activate the prepared OpenROAD/ORFS workspace
when it already provides the matching compiler and native dependency bundle;
its activation must make those tools visible through `PATH` and
`CMAKE_PREFIX_PATH` (or its equivalent).

```bash
P0_INPUT="$PWD/artifact_evaluation/lineage/openroad_power/p0/source"
P0_BUILD="$PWD/outputs/toolchain/openroad-p0"
rm -rf "$P0_BUILD"
mkdir -p "$(dirname "$P0_BUILD")"
cp -a "$P0_INPUT" "$P0_BUILD"
cd "$P0_BUILD"
# If the activated ORFS/GCC workspace already provides all native dependencies,
# skip both installer commands below.
sudo ./etc/DependencyInstaller.sh -base
./etc/DependencyInstaller.sh -common -local
# AE-1 needs the production executable; project-level validation is `make check` below.
./etc/Build.sh -no-tests
cd -

export OPENROAD_EXE="$P0_BUILD/build/bin/openroad"
make check
```

`-base` installs host packages and therefore requires an administrator-enabled
`sudo` session. If the host already provides the listed compiler and OpenROAD
build dependencies but does not grant `sudo` (a common shared-server setup),
skip only that `-base` line and run `-common -local`; ask the administrator to
install any missing host package rather than trying to work around privileges.
If an activated, compatible OpenROAD/ORFS workspace already supplies the full
native dependency bundle, skip **both** installer lines and run `Build.sh`
directly. This avoids downloading duplicate packages into `$HOME/.local`; it
does not replace the requirement that the host dependency versions be
compatible with the frozen p0 source.

Use `Build.sh -no-tests` for the release replay. It builds the production
OpenROAD executable needed by AE-1/AE-2/AE-3 while avoiding optional upstream
C++ unit-test targets. GoalEvolve's own release checks are run separately by
`make check` below.

`make check` runs the AE-1 preflight. It verifies the release manifest, p0 and
fixed-source snapshots, benchmark inputs, ASAP7 data, official parser/checker,
and Python interface. A separately prepared compatible OpenROAD environment can
be used instead by setting `OPENROAD_EXE` to its executable.

## AE-2: Reproduce a fixed evolved OpenROAD artifact

Each of the eight AE-2 artifacts contains its own frozen OpenROAD source
snapshot and recorded Tcl schedule. AE-2 rebuilds the selected snapshot, runs
its captured post-route flow, and compares its metrics and official 4/4
validity result with the fixed evidence. Required benchmark inputs, ASAP7 data,
the checker, and the frozen sources are included in the repository.

This is a **replay of an already selected result, not a new evolution run**:
it creates no Teacher/Student candidates and never changes the paper selection.
The rebuild is necessary because the selected result is a source-code change to
OpenROAD; each artifact must be compiled from its own immutable C++ snapshot
before its captured flow can be measured on a clean host.

First confirm that the p0 executable prepared by AE-1 starts in the current
environment. Then AE-2 stages and builds the selected artifact's own frozen
OpenROAD source before replaying its Tcl:

```bash
source outputs/toolchain/activate.sh
export OPENROAD_EXE="$PWD/outputs/toolchain/openroad-p0/build/bin/openroad"

PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae2/run_ae2.py preflight \
  --artifact aes_cipher_top_student_code --openroad "$OPENROAD_EXE" --verbose

# Build and replay this artifact's own immutable OpenROAD source snapshot.
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae2/run_ae2.py replay \
  --artifact aes_cipher_top_student_code --rebuild --jobs 8 --verbose
```

`--verbose` streams configure, build, and flow logs to the terminal. Each
artifact preserves its recorded evaluation mode; its numerical tolerances and
expected evidence are versioned in [release_manifest.json](artifact_evaluation/release_manifest.json).
See
[AE2_SELECTIONS.md](artifact_evaluation/AE2_SELECTIONS.md) for all artifact
IDs, source snapshots, Tcl schedules, QoR, and distances to target. Substitute
any listed ID for `aes_cipher_top_student_code` to replay that design.

`--openroad` is intentionally accepted only by the AE2 `preflight` mode: it checks that
the prepared host environment can launch OpenROAD. A formal AE-2 replay never
uses that external binary; it builds or reuses
`outputs/ae2/<released-artifact>/build/bin/openroad` from the selected source.

## AE-3: Run a new source-evolution campaign

AE-3 runs the complete Teacher/Student source-evolution workflow. It is
non-deterministic and is evaluated by valid workflow completion and measured
QoR, not by reproducing the released AES patch.

### Configure Codex access

Create the ignored project-local credential file and set its provider fields
and API key. Model, reasoning, retry, and timeout policy is shared by all
designs in [`config/codex.json`](config/codex.json).

```bash
cp config/credentials/goalevolve_codex.env.example \
  config/credentials/goalevolve_codex.env
chmod 600 config/credentials/goalevolve_codex.env
source outputs/toolchain/activate.sh
codex --version
```

### Add a design and profiles

Place the design inputs and create the two profiles below. ASAP7 technology data
is already shared at `third_party/benchmarks/asap7/`.

```text
third_party/benchmarks/benchmarks/my_design/
├── my_design.def or my_design.def.gz   # Placed input
├── my_design.v                         # Verilog netlist
├── my_design.sdc                       # Timing constraints
└── metrics.csv                         # Benchmark metadata
experiments/my_design/
├── baseline.json                       # p0 measurement profile
└── evolve.json                         # Multi-round evolution profile
```

```bash
mkdir -p experiments/my_design
cp config/templates/design.baseline.example.json experiments/my_design/baseline.json
cp config/templates/design.evolve.example.json experiments/my_design/evolve.json
```

Replace every `replace_design` with `my_design`. Leave `source_root` as `null`
to use repository p0, or set both profiles' `source_root` and `build_seed_root`
to a compatible OpenROAD workspace. `baseline.json` writes to
`outputs/baseline/my_design/`; `evolve.json` automatically writes to
`outputs/ae3/my_design/` unless `state_root` is explicitly set.

### Set the QoR contract

Run the baseline profile, then copy its measured metrics into
`evolve.json` `baseline_metrics`. Set `target_metrics` to the absolute TNS,
dynamic-power, and leakage-power limits for the new design, and set
`campaign_ready` to `true`. Both metric maps must use the same names.

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

### Start or resume the campaign

```bash
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli run \
  --config experiments/my_design/evolve.json --rounds 10
```

Run the same command again to append rounds to the same campaign. The shipped
AES and JPEG profiles already have reviewed targets; see
[experiments/README.md](experiments/README.md) for the other supplied designs.

### P0-rooted AE-3 (recommended)

The P0 command copies a reviewed template into a new isolated campaign,
measures and freezes its baseline, then starts evolution.  `ast_graph` is the
default evidence path; `openroad_cards` is the explicit no-AST ablation.  Both
commands require the credential file configured above and a working host
OpenROAD build environment from AE-1.

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli p0 list

# AST-repository-graph campaign
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli p0 start \
  --design aes_cipher_top --run-id aes_ast_10r --output-root outputs/p0_campaigns \
  --planning-mode ast_graph --rounds 10

# Comparable OpenROAD-card-only ablation; choose a different run ID.
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli p0 start \
  --design aes_cipher_top --run-id aes_cards_10r --output-root outputs/p0_campaigns \
  --planning-mode openroad_cards --rounds 10
```

Use `p0 status --campaign <campaign-directory>` to inspect a campaign and
`p0 run --campaign <campaign-directory> --rounds N` to resume it.  Generated
P0 campaigns stay under `outputs/` and are never a replacement for a fixed
AE-2 artifact.

## AE-4: Reproduce cross-design transfer

AE-4 uses the **locally rebuilt and passing** AES AE-2 executable on the seven
non-AES designs. It first verifies
`outputs/ae2/aes_cipher_top_student_code/report/ae2_report.json`, then generates its
evaluation-local RMP ABC Liberty from the bundled ASAP7 libraries. No machine
absolute path, prebuilt binary hash, or generated output is versioned.

```bash
source outputs/toolchain/activate.sh

# Required once after cloning: build and pass AES AE-2 as shown above.
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py prepare
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py run --jobs 1
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py collect
```

`--jobs 1` is the safe default for a shared host; raise it only when memory and
CPU capacity permit parallel OpenROAD flows. The seven generated baseline
flows, logs and summaries are ignored under `outputs/ae4/`. See
[artifact_evaluation/ae4/README.md](artifact_evaluation/ae4/README.md) for the fixed schedule and interpretation of
the resulting report.

## Outputs and result inspection

```text
outputs/
├── toolchain/                         # Project-local Python and Codex CLI
├── baseline/
│   └── <design>/                      # Measured p0 baseline evidence
├── ae2/
│   └── <released-artifact>/
│       ├── report/                    # Rebuild, preflight, and manifest comparison
│       └── contest_output/            # Flow logs, QoR metrics, and 4/4 result
├── ae3/
│   └── <design>/
│       ├── rounds/                    # Teacher plans and Student evaluations
│       ├── knowledge/                 # Persisted evolution evidence
│       └── parent.json                # Current promoted source parent
└── ae4/
    ├── provenance.json                # Validated AES AE-2 binary provenance
    ├── <design>/                      # Generated Tcl, flow log, and per-run JSON
    ├── summary.csv / summary.json     # Seven-design comparison rows
    ├── aggregate.json                 # Win counts and aggregate improvements
    ├── report.md                      # Human-readable AE-4 report
    └── table.tex                      # Paper-ready comparison table
```

For AE-2, inspect `report/ae2_report.json` for the fixed-artifact comparison
and `contest_output/` for the underlying flow. For AE-3, inspect the design's
`rounds/` and `parent.json` for candidate history and the current result. For
AE-4, inspect `outputs/ae4/report.md` and `summary.csv` for
the seven-design comparison; per-design logs and parsed results are under
`outputs/ae4/<design>/`.

<!--
## Testing and validation

Run the in-tree test suite after AE-1 setup:

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m pytest
```

Report AE-1 preflight, AE-2 replay/4/4 result, and AE-3 measured valid
candidates separately. A successful AE-2 replay validates the fixed artifact;
AE-3 validates a new stochastic evolution run.
-->

## Further documentation

- [Artifact evaluation](artifact_evaluation/README.md): AE-1 and AE-2 replay, plus the AE-3 execution pointer and evidence inventory.
- [AE-4 cross-design transfer](artifact_evaluation/ae4/README.md): transfer setup, fixed schedule, and report interpretation.
- [Toolchain lock](toolchain/README.md): version-lock policy and artifact boundary.
- [Implementation map](goalevolve/README.md): ownership of planning, execution, evaluation, and agents.
- [Configuration guide](config/README.md): profiles, path resolution, and credentials policy.
- [Paper](paper/GoalEvolve.pdf): framework, experimental setup, goal-attainment results, and AES case study.

<!--
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
-->


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
