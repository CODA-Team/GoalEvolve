# Configuration

`schema/experiment.schema.json` describes reviewed top-level fields. `templates/` contains portable starting points; `experiments/` contains design-specific profiles. Relative paths are resolved relative to the profile itself. A profile may use a shallow `extends` field for another reviewed profile in the experiment tree.

Each profile supplies `baseline_metrics` and absolute `target_metrics`; the runtime never derives a target by multiplying a ratio. `state_root` is optional and defaults to `outputs/ae3/<design>/`.

For a new design, copy both `templates/design.baseline.example.json` and
`templates/design.evolve.example.json` into `experiments/<design>/`. Run the
baseline profile first; it writes `outputs/baseline/<design>/baseline.json`.
Copy its measured decision metrics into `evolve.json`, set the absolute target
metrics, and change `campaign_ready` to `true`. The evolve template begins
locked deliberately, so placeholder values cannot launch a campaign.

[`codex.json`](codex.json) is the committed, project-wide Teacher/Student policy: models, reasoning effort, retries, and timeouts. It applies to every design. Worker homes are created under each campaign `state_root`. The ignored `credentials/goalevolve_codex.env` is created locally from its committed `.example` template and is the canonical AE-3 credential source. It contains the API key and provider connection fields only. Use environment variables `GOALEVOLVE_OPENROAD_SEED` and `GOALEVOLVE_BENCHMARK_ROOT` for machine-specific defaults, or explicit relative paths in a reviewed profile.
