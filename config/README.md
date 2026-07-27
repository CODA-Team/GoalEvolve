# Configuration

`schema/experiment.schema.json` describes reviewed top-level fields. `templates/` contains portable starting points; `experiments/` contains design-specific profiles. Relative paths are resolved relative to the profile itself. A profile may use a shallow `extends` field only for another profile in the same directory.

For a new contest design, start from
`templates/contest2026.design.bootstrap.example.json`, set the three
same-flow baseline metrics, then run `goalevolve baseline`. The command builds
the source-only p0 snapshot in a private output workspace and records a 4/4
validated measurement. Freeze the measured metrics into the evolution profile
before starting the campaign; do not copy metrics from a different OpenROAD
source or flow stage.

Do not place keys, build outputs, or old runtime paths in this directory. The
sole exception is the ignored `credentials/goalevolve_codex.env`, which is
created locally from its committed `.example` template and is the canonical
AE-3 credential source. Use environment variables `GOALEVOLVE_OPENROAD_SEED`
and `GOALEVOLVE_BENCHMARK_ROOT` for machine-specific defaults, or explicit
relative paths in a reviewed profile.
