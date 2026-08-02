# Configuration

`schema/experiment.schema.json` describes reviewed top-level fields. `templates/` contains portable starting points; `experiments/` contains design-specific profiles. Relative paths are resolved relative to the profile itself. A profile may use a shallow `extends` field for another reviewed profile in the experiment tree.

Each profile supplies `baseline_metrics` and absolute `target_metrics`; the runtime never derives a target by multiplying a ratio. `state_root` is optional and defaults to `outputs/ae3/<design>/`.

`epd_max_reinforcement_attempts` sets the per-idea limit for Enhancer retries
after a `promising` measured result. It defaults to `2`, is persisted into each
new EPD idea, and does not limit Explorer or Integrator attempts.

`source_root` is optional. When it is omitted or `null`, a `contest_openroad`
profile uses the shared source snapshot
`artifact_evaluation/lineage/openroad_power/p0/source`; set
`GOALEVOLVE_OPENROAD_SEED` to replace that machine-wide default. To use a
design-specific source, set the same `source_root` in both that design's
`baseline.json` and `evolve.json`. The path must name an OpenROAD source root
containing `CMakeLists.txt`, not a built `openroad` executable.

For a new design, copy both `templates/design.baseline.example.json` and
`templates/design.evolve.example.json` into `experiments/<design>/`. Run the
baseline profile first; it writes `outputs/baseline/<design>/baseline.json`.
Copy its measured decision metrics into `evolve.json`, set the absolute target
metrics, and change `campaign_ready` to `true`. The evolve template begins
locked deliberately, so placeholder values cannot launch a campaign.

[`codex.json`](codex.json) is the committed, project-wide Teacher/Student policy: models, reasoning effort, retries, timeouts, Student repair budget, and Teacher Markdown-format repair budget. It applies to every design. Worker homes are created under each campaign `state_root`. The ignored `credentials/goalevolve_codex.env` is created locally from its committed `.example` template and is the canonical AE-3 credential source. It contains the API key and provider connection fields only. Use environment variables `GOALEVOLVE_OPENROAD_SEED` and `GOALEVOLVE_BENCHMARK_ROOT` for machine-specific defaults, or explicit relative paths in a reviewed profile.
