# AES Power-Regression Recovery Design

## Goal

Restore a meaningful AES P0 evolution campaign without discarding the AST graph
or weakening official QoR authority.  A fresh, correctly configured ten-round
campaign must obtain at least five distinct official parent promotions.  Every
promotion must have a real source diff, complete build/flow/metrics/LEC
evidence, zero post-route DRV, and a strict controller-approved QoR gain.

## Evidence and scope

The 2026-08-05 AES campaign is not a valid prompt/AST comparison.  Its local
configuration supplied `early_forced_reclaim`, `proportion=1.0`, and
`max_moves=1`; the production P0 profile uses `proportion=80.0` with no move
cap.  The old v2 AES trajectory uses the latter execution capacity but starts
from an imported, already improved parent, so it is a source of mechanism
evidence only and cannot be imported as a fresh-P0 result.

The recovery therefore changes controller behavior, prompt construction, and
test coverage only.  It does not change the benchmark Tcl, flow evaluator,
official 4/4 evidence requirement, DRV requirement, frozen contract, or the
rule of one parent selected per round.

## Architecture

### 1. Immutable effective execution profile

Campaign startup will construct one canonical execution-profile record from
the resolved experiment configuration.  It includes phase, reclaim proportion,
move cap, source configuration path/hash, and the profile expected by the P0
campaign.  The controller persists it before running the baseline and supplies
the identical record to all Student evaluations.  A P0 campaign that resolves
to a profile different from its declared profile fails before any Codex or
OpenROAD work begins; it cannot silently substitute a smoke configuration.

The audit record is observer-only.  It neither changes OpenROAD command
semantics nor can it promote a candidate.

### 2. AST evidence is additive, not a search fence

The repository graph remains enabled and continues to validate source anchors.
The teacher packet will include a compact execution-witness slice for the
currently dispatched command chain: entry command, phase, policy selected,
source hooks, and permitted subsystem boundary.  Historical EPD cards and
controller fallback ideas remain eligible when their source hooks resolve in
the graph.  The teacher must not reject a concrete, graph-resolvable mechanism
merely because it is absent from the focused AST slice; it must record the
distinct boundary and falsification rule.  Thus AST helps localize live code
without hiding proven-but-off-slice power paths.

### 3. Revalidatable v2 mechanism seeds

The controller will build read-only seed cards from explicit v2 implementation
diffs and their recorded observed QoR.  A seed contains its source hooks,
changed-boundary summary, expected phase signals, and a statement that its
historical QoR is non-authoritative.  Seeds may only be assigned if the current
parent's graph resolves their hooks.  Students still create a new source diff
and the existing evaluator re-runs the complete official flow from the fresh
P0 lineage.  No v2 source commit, parent, metric, or promotion state is copied
into the campaign.

## Data flow

```text
resolved P0 config -> effective-profile audit -> baseline / every candidate
                                  |
P0 AST graph -> execution witness + graph-resolvable v2 seeds -> Teacher
                                  |
Teacher plan -> existing assignment / candidate / official evaluator
                                  |
                         existing strict promotion policy
```

## Error handling

Malformed or missing profile fields reject launch with an actionable mismatch
error.  A missing graph anchor causes only that historical seed to be excluded,
not a fallback to an unverified path.  A candidate with incomplete evidence,
nonzero DRV, unchanged/non-improved QoR, or telemetry-only activation remains
unpromoted under the existing policy.

## Tests and acceptance

Unit tests will prove profile equality acceptance and mismatch rejection,
execution-witness rendering, seed graph-resolution filtering, and that a seed
cannot supply parent metrics or promotion authority.  Existing promotion tests
continue to prove 4/4 and DRV gates.

The final operational proof is a new AES campaign from the same P0 source and
official flow, using `early_forced_reclaim`, 80%, and unlimited moves.  Its
first ten normal rounds must contain at least five distinct rounds with a
controller-recorded `promoted_student`, all with the existing official evidence
and zero DRV.  The prior 1%/one-move campaign is retained only as invalidated
diagnostic evidence.
