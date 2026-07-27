# JPEG encoder experiment

`evolve.json` is a portable AE-3 launch profile. It uses the project-owned
OpenROAD p0, JPEG benchmark inputs, and the measured same-flow p0 record at
`outputs/baseline/jpeg_encoder/baseline.json`.

Regenerate that record when changing the source snapshot or flow:

```bash
PYTHONPATH=. python3 -m goalevolve.cli baseline \
  --config experiments/jpeg_encoder/baseline.json
```

```bash
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/jpeg_encoder/evolve.json --rounds 10
```

The measured p0 is TNS `70.08 ns`, dynamic power `278.839B pW`, and leakage
`161M pW`; the profile keeps the historical JPEG goals of `53 ns`, `250B pW`,
and `80M pW`. It uses the generic portable planning catalog. The two later
v2-only JPEG execution-champion cards are intentionally not assumed for this
new campaign; see `artifact_evaluation/MIGRATION_AUDIT.md`.
