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
│   │   └── openroad_power/p0/    # P0 source manifest and prebuilt rsz/rmp AST graph
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
| AE-2 | Rebuild one released evolved OpenROAD source and replay its captured result. |
| AE-3 | Launch a GoalEvolve campaign for a supplied design or rerun full source evolution. |

Use AE-1 and AE-2 to set up the release and reproduce one of its eight fixed
OpenROAD artifacts.

Use AE-3 to evolve a design toward predefined QoR goals.

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

Copy and build p0 to prepare the matching OpenROAD executable. This keeps the
frozen source snapshot unchanged; the dependency installer uses `sudo` only for
host packages, while `-local` keeps downloaded build dependencies under the
current user.

```bash
P0_INPUT="$PWD/artifact_evaluation/lineage/openroad_power/p0/source"
P0_BUILD="$PWD/outputs/toolchain/openroad-p0"
rm -rf "$P0_BUILD"
mkdir -p "$(dirname "$P0_BUILD")"
cp -a "$P0_INPUT" "$P0_BUILD"
cd "$P0_BUILD"
sudo ./etc/DependencyInstaller.sh -base
./etc/DependencyInstaller.sh -common -local
./etc/Build.sh
cd -

export OPENROAD_EXE="$P0_BUILD/build/bin/openroad"
make check
```

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

First confirm that the p0 executable prepared by AE-1 starts in the current
environment. Then AE-2 stages and builds the selected artifact's own frozen
OpenROAD source before replaying its Tcl:

```bash
source outputs/toolchain/activate.sh
export OPENROAD_EXE="$PWD/outputs/toolchain/openroad-p0/build/bin/openroad"

PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2-preflight \
  --artifact aes_r58_student1 --openroad "$OPENROAD_EXE" --verbose

# Build and replay this artifact's own immutable OpenROAD source snapshot.
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2 \
  --artifact aes_r58_student1 --rebuild --jobs 8 --verbose
```

`--verbose` streams configure, build, and flow logs to the terminal. Each
artifact preserves its recorded evaluation mode; its numerical tolerances and
expected evidence are versioned in [release_manifest.json](artifact_evaluation/release_manifest.json).
The selected modes include `power_then_timing`, NVDLA-A `power_only`, and
NVDLA-C `timing_only`. See
[AE2_SELECTIONS.md](artifact_evaluation/AE2_SELECTIONS.md) for all artifact
IDs, source snapshots, Tcl schedules, QoR, and distances to target. Substitute
any listed ID for `aes_r58_student1` to replay that design.

`--openroad` is intentionally accepted only by `ae2-preflight`: it checks that
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
└── ae3/
    └── <design>/
        ├── rounds/                    # Teacher plans and Student evaluations
        ├── knowledge/                 # Persisted evolution evidence
        └── parent.json                # Current promoted source parent
```

For AE-2, inspect `report/ae2_report.json` for the fixed-artifact comparison
and `contest_output/` for the underlying flow. For AE-3, inspect the design's
`rounds/` and `parent.json` for candidate history and the current result.

## Testing and validation

Run the in-tree test suite after AE-1 setup:

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m pytest
```

Report AE-1 preflight, AE-2 replay/4/4 result, and AE-3 measured valid
candidates separately. A successful AE-2 replay validates the fixed artifact;
AE-3 validates a new stochastic evolution run.

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
