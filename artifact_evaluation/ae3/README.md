# AE3: user-driven evolution

AE3 is the user-driven GoalEvolve experiment. It is intentionally not copied
into `artifact_evaluation`: the artifact-evaluation package only contains the
released AE1, AE2, and AE4 replay surfaces.

The executable entry point is:

```bash
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/<experiment>.json
```

The implementation lives in `goalevolve/`. Experiment configurations live in
`experiments/`. Running AE3 may create fresh evolution rounds and search
artifacts; those are separate from the frozen `expected/` and `lineage/`
directories published for artifact evaluation.
