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
| AES | `aes_cipher_top_student_code`, `student_code` | `lineage/aes_cipher_top/student_code/source/` | `expected/aes_cipher_top/student_code/evaluate.tcl` | `power_then_timing` |
| JPEG | `jpeg_encoder_student_code`, `student_code` | `lineage/jpeg_encoder/student_code/source/` | `expected/jpeg_encoder/student_code/evaluate.tcl` | `power_then_timing` |
| Ariane | `ariane_student_code`, `student_code` | `lineage/ariane/student_code/source/` | `expected/ariane/student_code/evaluate.tcl` | `power_then_timing` |
| MemPool | `mempool_group_student_code`, `student_code` | `lineage/mempool_group/student_code/source/` | `expected/mempool_group/student_code/evaluate.tcl` | `power_then_timing` |
| NVDLA-A | `nvdla_a_student_code`, `student_code` | `lineage/nvdla_a/student_code/source/` | `expected/nvdla_a/student_code/evaluate.tcl` | `power_then_timing` |
| NVDLA-C | `nvdla_c_student_code`, `student_code` | `lineage/nvdla_c/student_code/source/` | `expected/nvdla_c/student_code/evaluate.tcl` | `timing_only` |
| NVDLA-M | `nvdla_m_student_code`, `student_code` | `lineage/nvdla_m/student_code/source/` | `expected/nvdla_m/student_code/evaluate.tcl` | `power_then_timing` |
| NVDLA-P | `nvdla_p_student_code`, `student_code` | `lineage/nvdla_p/student_code/source/` | `expected/nvdla_p/student_code/evaluate.tcl` | `power_then_timing` |

Each selection record preserves the release source hash, source commit, QoR
contract, and replay evidence under `expected/<design>/student_code/`.

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
