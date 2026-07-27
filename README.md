# GoalEvolve: From Handcrafted Algorithm Priors to Goal-Driven Evolution of Physical Design Algorithms

An open-source goal-driven framework for evolving bounded OpenROAD C++ mechanisms under post-route QoR evaluation and validity checks.

The accompanying paper is [GoalEvolve.pdf](paper/GoalEvolve.pdf): *GoalEvolve: From Handcrafted Algorithm Priors to Goal-Driven Evolution of Physical Design Algorithms*.

<p align="center">
  <img src="images/goalevolve_overview_v3.png" alt="GoalEvolve overview: frozen QoR targets guide checkpoint diagnosis, bounded OpenROAD source evolution, full-flow evaluation, and evidence-based promotion." width="100%">
</p>

```text
Frozen QoR contract
  -> checkpoint diagnosis and retrieval
  -> Teacher plan and bounded Student C++ edit
  -> isolated OpenROAD build
  -> post-placement optimization + global routing
  -> official metric parsing + 4/4 validity check
  -> evidence database and promotion
```

## Repository layout

```text
GoalEvolve/
├── goalevolve/                 # GoalEvolve Python implementation and CLI
│   ├── agents/                  # Teacher/Student Codex workers
│   ├── planning/                # Diagnosis, retrieval, and evidence memory
│   ├── execution/               # Isolated workspaces and campaign engine
│   └── evaluation/              # Tcl generation, QoR parsing, and 4/4 checks
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

## Reproduction tracks

GoalEvolve separates a deterministic artifact claim from a fresh LLM-driven experiment.

| Track | Purpose | Network/API key | Expected result |
|---|---|---:|---|
| AE-1 | Check that the release, benchmark, checker, and host interfaces are present | No | Deterministic pass/fail |
| AE-2 | Rebuild and replay the frozen AES R54 Student 1 artifact | No | Deterministic within manifest tolerances |
| AE-3 | Launch a new Teacher/Student source-evolution campaign | Yes | Workflow completion; QoR is stochastic |

Use AE-1 and AE-2 to reproduce the released artifact. Use AE-3 only when a Codex-capable environment and an independent API credential are available.

## Environment

### Reference environment

The paper's eight-design experiment was run on Rocky Linux 8.10 with two Intel Xeon Platinum 8462Y+ processors and 314 GiB RAM, using ASAP7 7 nm data and an OpenROAD source base reported as commit `08f67ee5`. That is the paper experiment environment, not a promise that every host will obtain bit-identical results.

This release locks the replay artifact separately in [toolchain/lock.json](toolchain/lock.json): the frozen source snapshot is identified by OpenROAD revision `d231bd8f98d2a0adb8369002b2c1e7aa8e7877ed`; the observed host used Python 3.12.13, CMake 3.31.9, and GCC 13.3.1. AE-2 rebuilds that shipped source rather than using a system `openroad` binary.

### Required software

For AE-1 and AE-2, use a Linux host with:

- Python 3.11 or newer.
- CMake, a C++ compiler compatible with the frozen OpenROAD source, GNU Make or the CMake-selected build tool, and standard Unix utilities.
- The native libraries required by the shipped OpenROAD source. These are host build prerequisites; the project does not vendor system packages.
- `pytest` only when running the test suite.

For AE-3, additionally install the `codex` command-line client and provide a valid provider/API credential. The release intentionally does not install, download, or configure Codex for the user.

AES replay builds a full OpenROAD executable and runs a complete physical-design flow. Disk, memory, and wall time are design- and host-dependent. Start with a conservative build parallelism such as `--jobs 8`; do not assume that `nproc` is an appropriate build-job count on a shared machine.

### Python setup

GoalEvolve itself has no runtime Python package dependency beyond the standard library. A virtual environment is still recommended for test tooling:

```bash
cd /path/to/GoalEvolve
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip pytest

# The package is intentionally run in-tree; no editable install is required.
PYTHONPATH=. python -m pytest
```

Confirm the host tools before a replay:

```bash
python3 --version
cmake --version
c++ --version
```

## Inputs and data boundaries

The release includes the material required for the fixed AES artifact:

- `third_party/benchmarks/`: AES benchmark inputs and ASAP7 technology files used by AE-2.
- `third_party/official_checker/`: the official log parser and 4/4 validity checker snapshot.
- `artifact_evaluation/lineage/aes_cipher_top/r054_student1/source/`: the frozen OpenROAD source for the fixed replay.

The repository also carries AE-3 profiles for `aes_cipher_top`, `ariane`, `jpeg_encoder`, `mempool_group`, `nvdla_a`, `nvdla_c`, `nvdla_m`, and `nvdla_p`. Availability of a profile does not replace any license or access requirement for benchmark data on a separate deployment. See [experiments/README.md](experiments/README.md) before starting a new design.

## Quick start: fixed artifact

Run the release preflight first:

```bash
cd /path/to/GoalEvolve
PYTHONPATH=. python3 -m artifact_evaluation.runner ae1
```

AE-1 verifies the release manifest, frozen source, AES benchmark, ASAP7 files, official parser/checker, Python, and CMake. A system `openroad` on `PATH` is reported for convenience but is not required.

Then rebuild and replay the frozen AES artifact:

```bash
PYTHONPATH=. python3 -m artifact_evaluation.runner ae2 \
  --artifact aes_r54_student1 --rebuild --jobs 8
```

AE-2 performs the following steps:

1. Builds `artifact_evaluation/lineage/aes_cipher_top/r054_student1/source` into `outputs/ae2/aes_r54_student1/build/`.
2. Runs the captured, path-portable `evaluate.tcl` from the original placed AES inputs.
3. Executes repair, legalizes placement, performs global routing, and estimates routing parasitics.
4. Parses post-route TNS, dynamic power, and leakage power.
5. Runs the official reference 4/4 validity checker.
6. Compares the observed metrics to [the frozen manifest](artifact_evaluation/release_manifest.json) within the declared tolerances.

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
command -v codex
cd /path/to/GoalEvolve
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/aes_cipher_top/evolve.json --rounds 10
```

The profile controls source scope, build jobs, per-command timeout, absolute objective targets, and campaign state path. Model and reasoning policy is shared by all designs through `config/codex.json`. `state_root` is optional; when omitted, a campaign writes to `outputs/ae3/<design>/`. The supplied global policy uses `gpt-5.6-terra` with `xhigh` reasoning; this setting is not a claim that another model/provider will behave equivalently.

`run` is the only public command that starts a Teacher/Student evolution campaign. `baseline` measures a fixed p0 before preparing a new profile; `official-check` validates an existing post-flow result; `sfinal-observe` and `leaderboard` generate observer-only reports; `import-legacy` imports explicit metadata; and `smoke` is a mock-only test helper.

## Starting a different design

Every design follows the same two-profile process. A new design needs a
project-owned benchmark directory at
`third_party/benchmarks/benchmarks/<design>/` containing `<design>.def` or
`<design>.def.gz`, `<design>.v`, `<design>.sdc`, and `metrics.csv`, and uses
the shared p0 source in `artifact_evaluation/lineage/openroad_power/p0/source`.

Create the two profiles from the committed templates:

```bash
mkdir -p experiments/my_design
cp config/templates/design.baseline.example.json \
  experiments/my_design/baseline.json
cp config/templates/design.evolve.example.json \
  experiments/my_design/evolve.json
```

In both files, replace every `replace_design` with `my_design`. Leave
`campaign_ready` as `false` and leave the placeholder metric values in place
for the baseline command. Then measure p0:

```bash
PYTHONPATH=. python3 -m goalevolve.cli baseline \
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
or another Tcl schedule with a full-flow campaign contract. Start the campaign
after the review:

```bash
PYTHONPATH=. python3 -m goalevolve.cli run \
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
    build/                         rebuilt OpenROAD
    contest_output/                Tcl, log, DEF/ODB, metrics, 4/4 report
    report/ae2_report.json         artifact comparison
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
PYTHONPATH=. python3 -m pytest
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
- `PYTHONPATH=. python3 -m goalevolve.cli --help`: public command reference. `run` is the only command that performs fresh source evolution; the other commands measure, validate, import, or report on artifacts.
- [Paper](paper/GoalEvolve.pdf): framework, experimental setup, goal-attainment results, and AES case study.
