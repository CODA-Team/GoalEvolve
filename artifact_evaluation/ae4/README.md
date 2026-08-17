# AE4: cross-design transfer of the AES-evolved OpenROAD

AE4 evaluates one frozen executable—the OpenROAD binary selected by the AES
AE2 `aes_cipher_top_student_code` release artifact—on the seven non-AES contest designs. Every
design uses only `baseline_flow`: stock `repair_design` followed by stock
`repair_timing -setup`. Thus the transferred variable is the AES-evolved
OpenROAD binary, not an AES-specific or target-specific Tcl policy. Every run
uses the same benchmark input and measurement tail:

```tcl
set_placement_padding -global -left 0 -right 0
detailed_placement
improve_placement -max_displacement {5 1}
optimize_mirroring
check_placement -verbose
estimate_parasitics -placement
set_power_activity -global -activity 0.1 -duty 0.5
unset_power_activity -global
# post-placement report
global_route -skip_large_fanout_nets 300 -allow_congestion -congestion_iterations 50
estimate_parasitics -global_routing
set_power_activity -global -activity 0.1 -duty 0.5
unset_power_activity -global
# post-route report
```

The activity set/unset pair invalidates OpenSTA's cached instance-power
values without changing the default activity semantics.  It prevents the
post-placement power report from being reused after global-route parasitics
are installed.

Runtime is recorded only as execution telemetry.  It is excluded from all
QoR win counts, aggregates, and the schedule recommendation, which follow the
frozen TNS/leakage/dynamic-power contract.

AE4 has one prerequisite: replay the released AES AE2 artifact successfully
first. The script checks its `passed`, artifact ID and frozen source hash in
`outputs/ae2/aes_cipher_top_student_code/report/ae2_report.json`. This avoids both
machine-specific absolute paths and non-portable executable hashes (an ELF hash
naturally changes when the frozen source is rebuilt on another host).

Prepare generated Tcl and provenance, then run or resume the seven baseline
flows with:

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py prepare
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py run --jobs 1
```

`--jobs 1` is a safe shared-host default. Increase it only when the host has
enough memory and CPU for concurrent OpenROAD flows.

Rebuild the summaries from completed logs with:

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" artifact_evaluation/ae4/run_ae4.py collect
```

Generated Tcl, raw logs, per-run JSON, and summaries are placed under
`outputs/ae4/`. Table-1 baselines come from the contest benchmark
`metrics.csv` files and are recorded in `experiment.json`; they are not the
results of AE4's `baseline_flow` schedule.

The generated summary artifacts are:

- `outputs/ae4/summary.csv` and `outputs/ae4/summary.json`: one row per design, with
  absolute and percentage changes from the Table-1 baseline;
- `outputs/ae4/aggregate.json`: seven-design win counts and equal-weight,
  macro-mean, and pooled improvements;
- `outputs/ae4/report.md`: human-readable baselines, the seven post-route results,
  and the source-transfer interpretation;
- `outputs/ae4/table.tex`: a paper-ready LaTeX form of the seven-row comparison.
