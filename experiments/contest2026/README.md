# Portable Contest Design Profiles

Each `*.bootstrap.json` starts from the project-owned shared OpenROAD p0
source and project-owned contest inputs. Its `baseline_metrics` are imported
reference values from the supplied benchmark CSV, used only to instantiate the
measurement contract. Run `goalevolve baseline` to produce the authoritative
same-flow p0 record, then copy its three measured decision metrics into a
reviewed evolution profile before launching Students.

The profiles contain no path to `GoalEvolve_v2`, old runtime output, or an
external OpenROAD checkout. The source-only snapshot is rebuilt under
`outputs/` when no build cache is available.

The following are the reference metrics imported from each supplied benchmark
`metrics.csv`; they are explicit bootstrap inputs, not claims that the shared
p0 produces the same values on another host or toolchain.

| Design | TNS (ns) | Dynamic (pW) | Leakage (pW) |
| --- | ---: | ---: | ---: |
| `ariane` | 7568.43 | 640100000000 | 17900000000 |
| `jpeg_encoder` | 48.77 | 293826000000 | 174000000 |
| `mempool_group` | 3680.33 | 275930000000 | 3070000000 |
| `nvdla_a` | 203.23 | 164679000000 | 321000000 |
| `nvdla_c` | 55.58 | 583700000000 | 17300000000 |
| `nvdla_m` | 13.99 | 44538500000 | 61500000 |
| `nvdla_p` | 198.18 | 39694000000 | 306000000 |

JPEG has an additional verified same-flow p0 baseline at
`outputs/baseline/jpeg_encoder/baseline.json`: TNS `70.08 ns`, dynamic
`278839000000 pW`, leakage `161000000 pW`, zero DRV, and official 4/4 pass.
That measured record, rather than the imported reference row, is frozen in
`experiments/jpeg_encoder/evolve.json`.

For example, start JPEG with:

```bash
PYTHONPATH=. python3 -m goalevolve.cli baseline \
  --config experiments/contest2026/jpeg_encoder.bootstrap.json \
  --output outputs/baseline/jpeg_encoder
```

Then create `jpeg_encoder.evolve.json` by copying the bootstrap profile,
replacing its three reference metrics with the generated `baseline.json`
metrics, setting meaningful target ratios, and changing `state_root` to a new
campaign directory. Use the same process for every other profile.
