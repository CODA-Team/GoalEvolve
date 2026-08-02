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

| Design | Released artifact and parent | Immutable OpenROAD source | Captured Tcl schedule | Flow mode |
|---|---|---|---|---|
| AES | `aes_r54_student1`, `round_054:student_1` | `lineage/aes_cipher_top/r054_student1/source/` | `round_054/student_1/contest_output/evaluate.tcl` | `power_then_timing` |
| JPEG | `jpeg_r16_student1`, `round_016:student_1` | `lineage/jpeg_encoder/r016_student1/source/` | `round_016/student_1/contest_output/evaluate.tcl` | `power_then_timing` |
| Ariane | `ariane_r11_student3`, `round_011:student_3:electric_normalization` | `lineage/ariane/r011_student3/source/` | `round_011/student_3/contest_output/evaluate.tcl` | `power_then_timing` |
| MemPool | `mempool_aes_r58_parent`, `preexperiment:aes_best_r58_target_1850` | `lineage/mempool_group/aes_r58_parent/source/` | `power_then_timing/evaluate.tcl` | `power_then_timing` |
| NVDLA-A | `nvdla_a_r19_student1`, `round_019:student_1` | `lineage/nvdla_a/r019_student1/source/` | `round_019/student_1/contest_output/evaluate.tcl` | `power_only` |
| NVDLA-C | `nvdla_c_rmp_path_cone_halo_timing`, `schedule:rmp_path_cone_halo_timing` | `lineage/nvdla_c/rmp_path_cone_halo_timing/source/` | `timing_only_rmp_path_cone_halo_timing/evaluate.tcl` | `timing_only` |
| NVDLA-M | `nvdla_m_r2_student1`, `round_002:student_1` | `lineage/nvdla_m/r002_student1/source/` | `power_then_timing/evaluate.tcl` | `power_then_timing` |
| NVDLA-P | `nvdla_p_r1_student2`, `round_001:student_2` | `lineage/nvdla_p/r001_student2/source/` | `power_then_timing/evaluate.tcl` | `power_then_timing` |

The exact recorded campaign-relative Tcl location, source hash, source commit,
contract, and raw parent record are preserved in the corresponding
`expected/<design>/<selection>/ae2_selection.json` file.

## QoR and distance to target

Each cell below is `normalized residual / unmet target percentage`.  A zero
means that metric meets its target.  Parent QoR is the recorded decision QoR,
not a later replay measurement.

| Design | Parent QoR: TNS / dynamic / leakage | TNS residual / unmet | Dynamic residual / unmet | Leakage residual / unmet | Total distance |
|---|---:|---:|---:|---:|---:|
| AES | `15.79 / 335.9714 / 28.6` | `0.426434 / 31.59%` | `0 / 0%` | `0 / 0%` | `0.142145` |
| JPEG | `52.45 / 263.885 / 115` | `0 / 0%` | `0.047256 / 5.55%` | `0.201149 / 43.75%` | `0.082802` |
| Ariane | `689.13 / 594.1 / 17900` | `0 / 0%` | `0 / 0%` | `0.016760 / 1.70%` | `0.005587` |
| MemPool | `2746.05 / 249.94 / 3060` | `0.243470 / 48.44%` | `0.235350 / 35.10%` | `0.602606 / 152.89%` | `0.360475` |
| NVDLA-A | `202.37 / 121.751 / 249` | `0 / 0%` | `0 / 0%` | `0.737091 / 437.80%` | `0.245697` |
| NVDLA-C | `8.93 / 583.7 / 17300` | `0 / 0%` | `0.001199 / 0.12%` | `0.046243 / 4.85%` | `0.015814` |
| NVDLA-M | `18.14 / 34.7636 / 36.4` | `0.599071 / 74.42%` | `0 / 0%` | `0.088388 / 16.29%` | `0.229153` |
| NVDLA-P | `160.62 / 37.743 / 257` | `0 / 0%` | `0.035599 / 3.69%` | `0.694891 / 285.89%` | `0.243496` |

Run an individual artifact with:

```bash
PYTHONPATH=. python3 -m artifact_evaluation.runner ae2 \
  --artifact <released-artifact-id> --openroad "$OPENROAD_EXE" --verbose
```

The available artifact IDs are the first values in the table above and can
also be obtained from `artifact_evaluation/release_manifest.json`.
