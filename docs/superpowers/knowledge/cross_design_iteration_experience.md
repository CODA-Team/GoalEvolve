# Cross-Design Iteration Experience Library

This is the human-readable companion to `cross_design_iteration_experience.json`.
The JSON file is the bounded, runtime-loaded prompt artifact; this document
explains how to add lessons without turning one campaign's patch or QoR into
global authority.

## Existing mechanisms and boundary

GoalEvolve already records campaign-local evidence in `knowledge/epd.json`,
`knowledge/observations.json`, Student reflections, and timing schedule memory.
Teacher plan/review packets consume those campaign-local records, while Student
packets consume their assigned EPD dossiers. None is a cross-design experience
library, so a lesson from one campaign was not previously available to another
campaign's Teacher or Student.

The checked-in JSON library fills only that gap. It is read by the Teacher's
draft, planning, and review prompts and by every Student packet. It is
advisory: it cannot alter a source boundary, the frozen QoR contract, the
promotion policy, or official-evidence requirements.

## Entry protocol

Add an entry after a completed official-flow experiment exposes a reusable
process lesson. Include its applicable stage, concise measured evidence, a
planning rule, a Student implementation rule, and a review rule. Keep exact
diffs, full metrics, and design-specific provenance in the campaign EPD.

Do not add a patch recipe, an unverified intuition, or a rule that weakens a
Controller-owned QoR constraint.

## AES P0 reflection, 2026-08-14

AES P0 R1 promoted a leakage-first ordering mechanism. In R2-R8, 21 candidates
completed official flow, but 14 reproduced the R1 parent's post-repair-power
replacement list exactly. Several source diffs emitted activation telemetry but
did not change the selected cell set; the remaining altered sets either kept
power unchanged or increased leakage. R3 also lost two planned Explorers at
source-anchor admission, so its intended direct-power coverage never ran.

The reusable lessons are recorded as:

- `XDES_SOURCE_ANCHOR_COVERAGE_001`
- `XDES_POWER_CELLSET_EFFECT_001`
- `XDES_POWER_STAGE_OBJECTIVE_ALIGNMENT_001`

They preserve the original power-first semantics: before power targets are
met, a timing-only improvement remains useful evidence but is not a promotion
candidate.
