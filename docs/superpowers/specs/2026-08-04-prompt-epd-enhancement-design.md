# Prompt and EPD Enhancement Design

## Goal

Strengthen GoalEvolve's Teacher and Student Codex workflow so that each
evolutionary decision is compact, evidence-grounded, retrievable, auditable,
and useful to the next round without changing frozen QoR or promotion
authority.  The design preserves the existing `knowledge/epd.json` campaign
database and materializes a path-addressable EPD view beside it; it does not
modify the repository's outer README.

## Constraints and evidence

- The Controller exclusively owns source validation, recipe selection,
  evidence classification, and promotion.  Prompt text must not grant those
  decisions to Codex.
- Teacher receives a small decision packet plus paths to evidence, never a
  copied full EPD, complete source graph, or complete historical diff.
- A real Student execution creates one readable Student reflection.  If
  telemetry is missing or ambiguous, the same Student investigates before the
  EPD falls back to `unactivated`.
- Existing campaigns and role selection continue to consume `epd.json`.
  Per-object files and indexes are a deterministic projection, not a risky
  database replacement.
- Search results are deterministic and auditable.  No embedding service or
  trained model is required for correctness; structured signature fields plus
  token overlap provide the initial retrieval ranking.

## Architecture

### Teacher decision packet

`TeacherPacketBuilder` projects current Controller state into a bounded
Markdown packet.  It begins with a usage guide; each later section has a
one-to-two sentence local instruction.  The guide covers Goal Contract,
Active Decision Stage, Diagnosis, EPD, Observation Memory, timing memory,
previous Student reflections, role envelopes, source localization,
evidence-only policy, paper cards, and assignments.

The packet uses a flat Goal Contract (design, immutable baseline and target
metrics), a short Active Decision Stage summary followed by reduced JSON, and
a Goal-local parent QoR line.  Diagnosis contains only nonduplicated evidence,
including checkpoint effects and their interpretation.  Source and policy
sections inline a focused view plus manifest paths.  The full payloads remain
on disk and are loaded only when the Teacher needs them.

Explorer planning is two pass: pass A emits five structured draft signatures;
the Controller searches EPD for every signature and writes a retrieval packet;
pass B reads those packets and emits the final Markdown plan.  Every final
Explorer idea contains query, retrieved IDs, opened paths, nearest historical
idea, semantic overlap, material difference, and novelty conclusion.  The
Controller rejects an Explorer assignment with missing trace evidence.

### EPD projection and search

`EvolutionProgramDatabase` remains the writer of `knowledge/epd.json`.  On
idea registration and attempt recording it also writes:

```
knowledge/epd/
  manifest.json
  ideas/<idea_id>/idea.json
  attempts/<attempt_id>/{attempt.json,stage_metrics.json,phase_signals.json,
                         student_reflection.md,implementation.diff,
                         evidence_manifest.json}
  mechanisms/<mechanism_id>/mechanism_card.json
  indexes/{idea_catalog.jsonl,retrieval_corpus.jsonl,by_status,by_stage,
           by_source_hook}
  round_views/round_NNN/{explorer_view.json,enhancer_view.json,
                         integrator_view.json}
```

An idea records its natural-language proposal and the decomposed
`observed_state`, `decision_boundary`, `proposed_action`, and
`acceptance_or_rollback_rule`.  An attempt contains immutable execution
evidence and artifact paths.  A mechanism card aggregates source read/write
sets, action type, dependencies, conflicts, compatibility and retained QoR
effects.  The projection is idempotent and rebuildable from `epd.json`.

`goalevolve.epd_search` supplies `search`, `show`, `compare`, and
`compatible-pairs`.  Search ranks exact stage/source-hook/decision-type
agreement before normalized token overlap, returns stable top-K candidates,
and appends JSONL trace events under the current round.  The controller's
audit requires a per-idea query, top-8 result, opened top-3 result IDs, and
all same-hook/same-decision-type records.

### Student loop and reflection

The Student packet has a role-specific operating protocol.  Explorer receives
retrieval obligations and a new-mechanism boundary; Enhancer receives a compact
candidate directory plus the previous round's complete semantic dossier;
Integrator receives compatibility fields and must inspect every mechanism
card.  Paths replace copied diffs and object logs.

After evaluation, `CodexStudentEditor` resumes the same Student identity for a
reflection turn.  The turn writes a Markdown paragraph explaining intended
mechanism, actual source change, activation and checkpoint effects, final QoR
effect, limitations, re-use guidance, next refinement, contraindications and
a recommended lifecycle state.  A separate advisory scheduling-decision
artifact is also persisted when applicable.  The EPD keeps the reflection path
and the projection copies the reflection into the attempt directory.

If the evaluator cannot observe expected telemetry, the same Student receives
the failure context and must distinguish parser/telemetry failure from an
inactive mechanism.  Evidence can select `validated`, `promising`, `pending`,
or `invalid` only under the Controller's deterministic guards.  `unactivated`
remains reserved for genuine unresolved ambiguity after this investigation.

### Auxiliary Codex narrator

`CodexNarrativeSummarizer` is a registered, observer-only plugin distinct from
Teacher and Student.  It can summarize a bounded evidence bundle when a
Controller workflow explicitly requests narrative diagnosis.  It writes an
artifact and may not edit source, choose recipes, classify evidence, or
promote.  Default execution does not call it for already structured evidence.

## Error handling and migration

Malformed/missing EPD projection files trigger a deterministic rebuild from
`epd.json`; malformed search queries fail with a nonzero CLI status and no
trace claim.  Missing historical artifacts appear as paths with a `missing`
marker rather than invented content.  Failure of the optional narrator is
recorded as observer metadata and never changes a candidate verdict.  Old EPD
rows receive empty decomposed fields and remain searchable by their legacy
claim and hooks.

## Verification strategy

Unit tests prove packet size/section semantics, EPD object projection,
deterministic search and trace audit, role packet contents, reflection parsing,
unactivated fallback, and narrator authority boundaries.  Existing controller
tests remain green after the pre-existing scheduling-decision helper is
implemented.  The complete suite, `epd_search --help`, a mock two-pass campaign
and an AES P0 baseline/campaign are run after implementation.  The actual AES
campaign uses its own state root, at most eight rounds, and stops after three
consecutive no-promotion rounds.
