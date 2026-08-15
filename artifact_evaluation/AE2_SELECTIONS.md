# AE-2 Selected Parent Artifacts

Each released AE-2 artifact is the selected parent nearest to that design's
frozen QoR contract in the recorded `GoalEvolve_v2` campaign.  The release
ships its own immutable OpenROAD source snapshot under `lineage/`, its
path-normalized captured Tcl under `expected/`, and the machine-readable
selection record in `ae2_selection.json`.

`TNS` is in ns, dynamic power is in mW, and leakage power is in uW.  The
reported total is the mean of the three normalized residuals.  A residual is
`max(0, measured - target) / max(abs(baseline - target), abs(baseline), 1)`;
the unmet percentage is `max(0, measured - target) / target * 100`.

## Selected sources and flows

| Design | Released artifact and parent | Immutable OpenROAD source | GitHub replay Tcl | Flow mode |
|---|---|---|---|---|
| AES | `aes_r58_student1`, `round_058:student_1` | `lineage/aes_cipher_top/r058_student1/source/` | `expected/aes_cipher_top/r058_student1/evaluate.tcl` | `power_then_timing` |
| JPEG | `jpeg_r16_student1`, `round_016:student_1` | `lineage/jpeg_encoder/r016_student1/source/` | `expected/jpeg_encoder/r016_student1/evaluate.tcl` | `power_then_timing` |
| Ariane | `ariane_r11_student3`, `round_011:student_3:electric_normalization` | `lineage/ariane/r011_student3/source/` | `expected/ariane/r011_student3/evaluate.tcl` | `power_then_timing` |
| MemPool | `mempool_aes_r58_parent`, `preexperiment:aes_best_r58_target_1850` | `lineage/mempool_group/aes_r58_parent/source/` | `expected/mempool_group/aes_r58_parent/evaluate.tcl` | `power_then_timing` |
| NVDLA-A | `nvdla_a_r16_student1`, `round_016:student_1` | `lineage/nvdla_a/r016_student1/source/` | `expected/nvdla_a/r016_student1/evaluate.tcl` | `power_then_timing` |
| NVDLA-C | `nvdla_c_rmp_path_cone_halo_timing`, `schedule:rmp_path_cone_halo_timing` | `lineage/nvdla_c/rmp_path_cone_halo_timing/source/` | `expected/nvdla_c/rmp_path_cone_halo_timing/evaluate.tcl` | `timing_only` |
| NVDLA-M | `nvdla_m_r11_student1`, `round_011:student_1` | `lineage/nvdla_m/r011_student1/source/` | `expected/nvdla_m/r011_student1/evaluate.tcl` | `power_then_timing` |
| NVDLA-P | `nvdla_p_r4_student1`, `round_004:student_1` | `lineage/nvdla_p/r004_student1/source/` | `expected/nvdla_p/r004_student1/evaluate.tcl` | `power_then_timing` |

The exact recorded campaign-relative Tcl location, source hash, source commit,
contract, and raw parent record are preserved in the corresponding
`expected/<design>/<selection>/ae2_selection.json` file.

## QoR and distance to target

Each cell below is `normalized residual / unmet target percentage`. A zero
means that metric meets its target. Parent QoR is the cache-safe post-route
replay measurement used by the released AE-2 checker: it is collected after
global-route parasitics and an explicit OpenSTA power-cache refresh.

| Design | Parent QoR: TNS / dynamic / leakage | TNS residual / unmet | Dynamic residual / unmet | Leakage residual / unmet | Total distance |
|---|---:|---:|---:|---:|---:|
| AES | `15.5726 / 335.6071 / 29.093` | `0.401979 / 29.78%` | `0 / 0%` | `0 / 0%` | `0.133993` |
| JPEG | `51.1753 / 270.4277 / 114.525` | `0 / 0%` | `0.069523 / 8.17%` | `0.198420 / 43.16%` | `0.089314` |
| Ariane | `688.5370 / 588.0586 / 17935.077` | `0 / 0%` | `0 / 0%` | `0.018719 / 1.90%` | `0.006240` |
| MemPool | `2714.6869 / 249.5864 / 3059.457` | `0.234948 / 46.74%` | `0.234068 / 34.91%` | `0.602429 / 152.85%` | `0.357148` |
| NVDLA-A | `90.6404 / 144.2286 / 285.284` | `0 / 0%` | `0.129533 / 14.47%` | `0.869033 / 516.16%` | `0.332855` |
| NVDLA-C | `7.2477 / 584.0860 / 17259.404` | `0 / 0%` | `0.001861 / 0.19%` | `0.043896 / 4.60%` | `0.015252` |
| NVDLA-M | `14.2097 / 39.4733 / 38.326` | `0.294866 / 36.63%` | `0.100218 / 11.82%` | `0.121768 / 22.45%` | `0.172284` |
| NVDLA-P | `163.4940 / 38.2315 / 256.406` | `0 / 0%` | `0.048547 / 5.03%` | `0.692723 / 284.99%` | `0.247090` |

Run an individual artifact with:

```bash
PYTHONPATH=. python3 -m artifact_evaluation.runner ae2 \
  --artifact <released-artifact-id> --openroad "$OPENROAD_EXE" --verbose
```

The available artifact IDs are the first values in the table above and can
also be obtained from `artifact_evaluation/release_manifest.json`.
