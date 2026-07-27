# GoalEvolve_v2 Migration Audit

This audit compares `/home/haixuliu/MLCAD26/GoalEvolve_v2/goalevolve_v2`
with the reorganized `goalevolve` package. It distinguishes module import
relocation from AST-level class and function-definition changes.

Run the machine-checkable audit from the project root:

```bash
python3 artifact_evaluation/audit_migration.py \
  --reference ../GoalEvolve_v2 --format markdown
```

## Mapping and Result

The following 21 modules have identical normalized bodies and identical
class/function definitions. Their only change is the package import path:

```text
contracts -> core/contracts          io -> core/io
models -> core/models                plugins -> core/plugins
provenance -> core/provenance        diagnosis -> planning/diagnosis
epd -> planning/epd                  observations -> planning/observations
scope -> planning/scope              prompting -> agents/prompting
execution -> execution/execution     preflight -> execution/preflight
workspace -> execution/workspace     engine -> execution/engine
evidence -> evaluation/evidence      leaderboard -> evaluation/leaderboard
promotion -> evaluation/promotion    evaluators -> testing/evaluators
cli -> cli                           legacy -> legacy
token_ledger -> token_ledger
```

`sfinal -> evaluation/sfinal` also retains identical definitions. Its only
body change is the vendored official-cell-list path, from v2's `vendor/` to
this project's `third_party/`.

## Deliberate Differences

| Area | Difference | Impact on AE-3 core evolution |
| --- | --- | --- |
| `config` | Project-relative defaults, shallow reviewed-profile inheritance, and explicit `credential_env`. | No planning/promotion behavior change; removes v2 machine-specific paths. |
| `teacher`, `codex_student`, `codex_runtime` | Project-local worker home and explicit canonical credential dotenv. | Same prompts, retries, session handling, and edit protocol; credentials no longer depend on v2 runtime paths. |
| `contest2026` | Official files move to `third_party`; a source-only p0 is privately built for baseline; no-diff COW reuses a compatible donor binary. | Candidate flow, official parser and 4/4 gating are unchanged. The added behavior makes supplied p0 source snapshots runnable without a prebuilt binary. |
| `retrieval`, `timing_recovery` | v2 contains two later JPEG execution-champion cards and its `mt1_mid_power` recipe; they are intentionally absent here. | These cards encode an old JPEG-only source+Tcl operating point and are not a general multi-design mechanism. All generic planner cards and recipes remain. |

Therefore the generic AE-3 controller, planning, workspace isolation, Student
repair loop, evidence gate, promotion and candidate evaluation logic have been
preserved. The standalone project is suitable for new designs after each design
records a same-flow p0 baseline. Reinstating the two historical JPEG cards is
only appropriate when reproducing their exact old JPEG campaign and its
controller-owned recipe; it should not be silently enabled for other designs.
