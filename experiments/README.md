# AE-3 Design Profiles

Every new p0 design uses the same profile model. [`ae3_base.json`](ae3_base.json)
owns the shared source snapshot, benchmark root, workers, evaluator, patch
scope, and resource policy. Each `<design>/` directory uses these two files:

```text
experiments/
├── ae3_base.json                 # Shared AE-3 defaults for every design
└── <design>/
    ├── baseline.json             # Same-flow p0 measurement; writes outputs/baseline/<design>/
    └── evolve.json               # Reviewed multi-round campaign; writes outputs/ae3/<design>/
```

AES is the one deliberate source exception: its `evolve.json` starts from the
immutable R54 parent retained for fixed-artifact continuity. It still inherits
the same worker, evaluator, objective, state-root, and multi-round policy.

`baseline_metrics` and `target_metrics` always use the identical, explicit
metric names. Targets are absolute values; the runtime never calculates a
target from a ratio. A profile is rejected when either metric map has a missing
or unexpected decision metric.

## Reviewed AE-3 targets

All values below are the exact `target_metrics` values in the corresponding
`evolve.json`. TNS is in ns; dynamic and leakage power are in pW.

| Design | TNS | Dynamic power | Leakage power | Status |
| --- | ---: | ---: | ---: | --- |
| `aes_cipher_top` | 12 | 350000000000 | 35000000 | launchable |
| `jpeg_encoder` | 53 | 250000000000 | 80000000 | launchable |
| `ariane` | 1850 | 623000000000 | 17500000000 | baseline required |
| `nvdla_c` | 10 | 583000000000 | 16500000000 | baseline required |
| `nvdla_p` | 246 | 36400000000 | 66600000 | baseline required |
| `mempool_group` | 1 | 1 | 1 | temporary target; baseline required |
| `nvdla_a` | 1 | 1 | 1 | temporary target; baseline required |
| `nvdla_m` | 1 | 1 | 1 | temporary target; baseline required |

## Prepare a design

For a design whose `evolve.json` has `"campaign_ready": false`, first run its
checked-in baseline profile:

```bash
PYTHONPATH=. python3 -m goalevolve.cli baseline \
  --config experiments/mempool_group/baseline.json
```

Read `outputs/baseline/mempool_group/baseline.json`, replace the three values
in `experiments/mempool_group/evolve.json` `baseline_metrics`, choose and set
its absolute `target_metrics`, and then set `"campaign_ready": true`.
`baseline_evaluation_root` is already set to that evidence directory. This
explicit gate prevents an imported benchmark CSV row from accidentally starting
an evolution campaign.

`aes_cipher_top` and `jpeg_encoder` have reviewed targets and are launchable.
The other supplied designs have portable inputs and profiles, but remain gated
until their same-flow p0 measurement is performed on the intended host.

## Run and resume

```bash
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/mempool_group/evolve.json --rounds 10
```

`evolve.json` deliberately omits `state_root`, so it defaults to
`outputs/ae3/<design>/`. Repeating the command with the same profile resumes
from its last completed round and appends the requested number of rounds. Add a
unique `state_root` only when a separate campaign for the same design is
intended. `max_campaign_rounds` is `null` in the shared profile, so no hidden
round cap prevents a multi-round campaign.
