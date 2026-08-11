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
| NVDLA-A | `nvdla_a_r19_student1`, `round_019:student_1` | `lineage/nvdla_a/r019_student1/source/` | `expected/nvdla_a/r019_student1/evaluate.tcl` | `power_only` |
| NVDLA-C | `nvdla_c_rmp_path_cone_halo_timing`, `schedule:rmp_path_cone_halo_timing` | `lineage/nvdla_c/rmp_path_cone_halo_timing/source/` | `expected/nvdla_c/rmp_path_cone_halo_timing/evaluate.tcl` | `timing_only` |
| NVDLA-M | `nvdla_m_r11_student1`, `round_011:student_1` | `lineage/nvdla_m/r011_student1/source/` | `expected/nvdla_m/r011_student1/evaluate.tcl` | `power_then_timing` |
| NVDLA-P | `nvdla_p_r4_student1`, `round_004:student_1` | `lineage/nvdla_p/r004_student1/source/` | `expected/nvdla_p/r004_student1/evaluate.tcl` | `power_then_timing` |

The exact recorded campaign-relative Tcl location, source hash, source commit,
contract, and raw parent record are preserved in the corresponding
`expected/<design>/<selection>/ae2_selection.json` file.

## QoR and distance to target

Each cell below is `normalized residual / unmet target percentage`.  A zero
means that metric meets its target.  Parent QoR is the recorded decision QoR,
not a later replay measurement.

| Design | Parent QoR: TNS / dynamic / leakage | TNS residual / unmet | Dynamic residual / unmet | Leakage residual / unmet | Total distance |
|---|---:|---:|---:|---:|---:|
| AES | `15.68 / 340.9709 / 29.1` | `0.414061 / 30.68%` | `0 / 0%` | `0 / 0%` | `0.138020` |
| JPEG | `52.45 / 263.885 / 115` | `0 / 0%` | `0.047256 / 5.55%` | `0.201149 / 43.75%` | `0.082802` |
| Ariane | `689.13 / 594.1 / 17900` | `0 / 0%` | `0 / 0%` | `0.016760 / 1.70%` | `0.005587` |
| MemPool | `2746.05 / 249.94 / 3060` | `0.243470 / 48.44%` | `0.235350 / 35.10%` | `0.602606 / 152.89%` | `0.360475` |
| NVDLA-A | `202.37 / 121.751 / 249` | `0 / 0%` | `0 / 0%` | `0.737091 / 437.80%` | `0.245697` |
| NVDLA-C | `8.93 / 583.7 / 17300` | `0 / 0%` | `0.001199 / 0.12%` | `0.046243 / 4.85%` | `0.015814` |
| NVDLA-M | `14.49 / 39.3617 / 38.3` | `0.316563 / 39.33%` | `0.097538 / 11.51%` | `0.121317 / 22.36%` | `0.178473` |
| NVDLA-P | `165.79 / 37.744 / 256` | `0 / 0%` | `0.035625 / 3.69%` | `0.691241 / 284.38%` | `0.242289` |

Run an individual artifact with:

```bash
PYTHONPATH=. python3 -m artifact_evaluation.runner ae2 \
  --artifact <released-artifact-id> --openroad "$OPENROAD_EXE" --verbose
```

The available artifact IDs are the first values in the table above and can
also be obtained from `artifact_evaluation/release_manifest.json`.
