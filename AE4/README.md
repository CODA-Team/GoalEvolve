# AE4: cross-design transfer of the AES-evolved OpenROAD

AE4 evaluates one frozen executable—the OpenROAD binary selected by the AES
AE2 `round_058:student_1` parent—on the seven non-AES contest designs.  Each
design is evaluated with three optimization schedules:

1. `baseline_flow`: stock `repair_design` followed by stock
   `repair_timing -setup`;
2. `aes_schedule`: the optimization sequence captured by the selected AES
   AE2 parent;
3. `design_schedule`: the optimization sequence captured by that design's
   selected AE2 parent, still executed by the AES-evolved binary.

Only the optimization schedule changes.  Every run uses the same benchmark
input and the same measurement tail:

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
first.  The script checks its `passed`, artifact ID and frozen source hash in
`outputs/ae2/aes_r58_student1/report/ae2_report.json`; it then builds the
evaluation-local RMP Liberty from `third_party/benchmarks/asap7/lib/`.  This
avoids both machine-specific absolute paths and non-portable executable hashes
(an ELF hash naturally changes when the frozen source is rebuilt on a different
host).

Prepare generated Tcl and provenance, then run or resume the complete
experiment with:

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" AE4/run_ae4.py prepare
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" AE4/run_ae4.py run --jobs 1
```

`--jobs 1` is a safe shared-host default. Increase it only when the host has
enough memory and CPU for concurrent OpenROAD flows.

Rebuild the summaries from completed logs with:

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" AE4/run_ae4.py collect
```

Generated Tcl, raw logs, per-run JSON, and summaries are placed under
`AE4/results/`.  Table-1 baselines come from the contest benchmark
`metrics.csv` files and are recorded in `experiment.json`; they are not the
results of AE4's `baseline_flow` schedule.

The generated summary artifacts are:

- `results/summary.csv` and `results/summary.json`: one row per design and
  schedule, with absolute and percentage changes from the Table-1 baseline;
- `results/aggregate.json`: schedule-level win counts and equal-weight,
  macro-mean, and pooled improvements;
- `results/report.md`: human-readable baselines, all 21 post-route results,
  and the cross-design interpretation;
- `results/table.tex`: a paper-ready LaTeX form of the 21-row comparison.

For the source-transfer claim, `baseline_flow` is the primary condition: it
contains no AES-specific or target-design-specific scheduling policy, so its
change from the contest baseline can be attributed to the AES-evolved binary
under a stock optimization sequence.  `aes_schedule` is retained as a direct
schedule-transfer stress test, and `design_schedule` as a source-plus-target-
schedule control rather than as pure source-generalization evidence.
