# Prompt and EPD Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver compact, evidence-routed Teacher and Student prompting, searchable EPD projection, audited novelty review, Student reflections, and P0 AES verification without editing the outer README.

**Architecture:** Retain `knowledge/epd.json` as the compatible campaign record and project it into `knowledge/epd/` objects and indexes.  Teacher packet construction, EPD retrieval, and Student reflection have narrow modules; `engine.py` only orchestrates them and remains the Controller authority.

**Tech Stack:** Python 3.11, stdlib JSON/argparse/pathlib, existing `PersistentCodexRunner`, unittest, tree-sitter for existing graph tests.

---

## File map

- Create: `goalevolve/planning/epd_search.py` — deterministic EPD query,
  comparison, pair compatibility, trace persistence, and CLI entry point.
- Create: `goalevolve/agents/narrator.py` — observer-only auxiliary Codex
  narrative plugin.
- Modify: `goalevolve/planning/epd.py` — projection, object cards, indexes,
  round views, and reflection persistence.
- Modify: `goalevolve/agents/teacher.py` — decision-packet projection,
  two-pass Explorer planning, and retrieval audit requirements.
- Modify: `goalevolve/agents/prompting.py` — concise role-specific EPD
  packets and explicit Student reflection contract.
- Modify: `goalevolve/agents/codex_student.py` — scheduling decision artifact
  repair and same-thread reflection turn.
- Modify: `goalevolve/execution/engine.py` — reflection orchestration,
  retrieval-audit enforcement, EPD ambiguity handling, and stop policy.
- Modify: `goalevolve/config.py`, `goalevolve/cli.py`, `goalevolve/core/plugins.py`
  — narrator registration, no-promotion stop setting, and `epd-search` CLI.
- Modify: `tests/unit/test_core.py`; create `tests/unit/test_epd_search.py` —
  all public behavior described below.

### Task 1: Establish the regression baseline and Student advisory artifact

**Files:**
- Modify: `goalevolve/agents/codex_student.py`
- Test: `tests/unit/test_core.py:GoalEvolveV2Tests.test_student_scheduling_decision_is_persisted_as_advisory_artifact`

- [ ] **Step 1: Run the existing narrow test and preserve its red result.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_student_scheduling_decision_is_persisted_as_advisory_artifact -q`

Expected: `AttributeError` for `_write_internal_cpp_scheduling_decision`.

- [ ] **Step 2: Implement only the missing static helper.**

The helper reads the latest message, accepts only `accepted|adapted|rejected`,
extracts the next nonempty rationale line, and atomically writes:

```python
{"suggestion": suggestion, "decision": decision, "rationale": rationale,
 "advisory_only": True}
```

to `artifact_root / "internal_cpp_scheduling_decision.json"`.

- [ ] **Step 3: Run the narrow test and the full suite.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core -q`

Expected: the baseline helper test passes and no unrelated test regresses.

### Task 2: Define deterministic EPD projection records

**Files:**
- Modify: `goalevolve/planning/epd.py`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing tests for a registered idea and recorded attempt.**

Assert that registration creates `knowledge/epd/ideas/<id>/idea.json`, an
attempt creates all six attempt artifacts plus a mechanism card, and rebuilding
the projection leaves byte-equivalent indexes.

- [ ] **Step 2: Run the focused projection tests.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_epd_projection_materializes_objects -q`

Expected: failure because `epd/manifest.json` does not exist.

- [ ] **Step 3: Add `_project()` and normalize legacy records.**

Use existing `atomic_json`, write decomposed idea fields, attempt metrics and
artifact references, one mechanism card per mechanism family, JSONL catalog and
retrieval corpus, then write a manifest only after all objects are present.

- [ ] **Step 4: Re-run focused projection tests.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_epd_projection_materializes_objects -q`

Expected: pass.

### Task 3: Build and test the EPD retrieval CLI

**Files:**
- Create: `goalevolve/planning/epd_search.py`
- Modify: `goalevolve/cli.py`
- Test: `tests/unit/test_epd_search.py`

- [ ] **Step 1: Write failing tests for search, show, compare, and pairs.**

Create three projected ideas with overlapping hooks and distinct decision
boundaries.  Assert search returns a same-stage/same-hook record before a
token-only match; assert `compare` identifies overlap and difference; assert
compatible pairs reject shared write sets; assert a query appends a trace row.

- [ ] **Step 2: Run the new search tests.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_epd_search -q`

Expected: import failure for `goalevolve.planning.epd_search`.

- [ ] **Step 3: Implement deterministic ranking and commands.**

Use a weighted tuple `(stage_match, hook_match, decision_match,
token_overlap, stable_id)` and expose `search --query-file --stage --top-k`,
`show --idea-id --include`, `compare --query-file --idea-id`, and
`compatible-pairs --parent --stage --top-k`.  Every search records query,
ordered results, opened IDs and timestamp in the supplied trace path.

- [ ] **Step 4: Run tests and help output.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_epd_search -q && PYTHONPATH=. python3 -m goalevolve.epd_search --help`

Expected: tests pass and help lists all four commands.

### Task 4: Project compact Teacher decision packets

**Files:**
- Create: `goalevolve/agents/teacher_packet.py`
- Modify: `goalevolve/agents/teacher.py`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing packet tests.**

Assert the packet starts with `## Packet Usage Guide`, includes every required
section exactly once, renders goal metrics as a flat list, has a stage summary
before reduced JSON, excludes duplicated Parent data from Diagnosis, provides
EPD object paths, and limits observation lessons to ten.

- [ ] **Step 2: Run the focused packet tests.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_teacher_packet_is_compact_and_evidence_routed -q`

Expected: failure because the existing packet directly JSON-dumps full state.

- [ ] **Step 3: Implement `TeacherPacketBuilder`.**

Build slot-specific projections, preserve Controller guard prose, include
focused graph paths, compact observation lessons, compact search policy and
role-specific EPD views.  Replace only `_plan_prompt`'s packet construction;
keep its Markdown output schema stable.

- [ ] **Step 4: Run packet regression tests.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core -q`

Expected: existing Teacher format tests and new packet tests pass.

### Task 5: Enforce two-pass Explorer novelty review

**Files:**
- Modify: `goalevolve/agents/teacher.py`
- Modify: `goalevolve/execution/engine.py`
- Modify: `goalevolve/planning/epd_search.py`
- Test: `tests/unit/test_core.py`, `tests/unit/test_epd_search.py`

- [ ] **Step 1: Write failing tests for retrieval audit rejection.**

Give a draft signature with no trace and assert its Explorer assignment is
rejected.  Add a trace with top-8 candidates, three opened records, and a
same-hook/same-boundary opening; assert it is accepted.  Assert the final
idea's novelty fields survive into `idea.json`.

- [ ] **Step 2: Run the narrow audit test.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_explorer_assignment_requires_retrieval_audit -q`

Expected: failure because the current controller does not inspect trace rows.

- [ ] **Step 3: Implement pass A, controller retrieval, pass B, and audit.**

Pass A requests only five draft signatures.  The Controller invokes the local
search API, writes `teacher_epd_retrieval_trace.jsonl` and a retrieval packet,
then pass B receives the packet and emits final assignments.  Reject only the
unsupported Explorer assignment; retain EPD roles with their Controller-owned
portfolios.

- [ ] **Step 4: Run the audit and Teacher suites.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_epd_search tests.unit.test_core -q`

Expected: all retrieval and Teacher protocol tests pass.

### Task 6: Make Student packets role-specific and path-routed

**Files:**
- Modify: `goalevolve/agents/prompting.py`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing role-packet tests.**

Assert Explorer receives signature/retrieval obligations and no copied history;
Enhancer receives a candidate directory and its previous-round dossier;
Integrator receives read/write sets, conflicts and diff/reflection paths.  In
all cases assert a `## Student Reflection` completion contract.

- [ ] **Step 2: Run the role-packet tests.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_student_packets_route_epd_evidence_by_role -q`

Expected: failure because current packets inline records and source bundles.

- [ ] **Step 3: Implement compact packet projections.**

Build a directory row for every eligible record; inline only the small
newly-enhanceable dossier.  Replace copied `added_code`/`removed_code` payloads
with source artifact paths.  State role responsibility before generic edit
constraints.

- [ ] **Step 4: Run focused and full unit suites.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core -q`

Expected: all Student packet tests pass.

### Task 7: Persist Student reflections and resolve ambiguous activation

**Files:**
- Modify: `goalevolve/agents/codex_student.py`
- Modify: `goalevolve/execution/engine.py`
- Modify: `goalevolve/planning/epd.py`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing reflection lifecycle tests.**

Use a fake runner returning a reflection message.  Assert the same Student
identity is resumed, `student_reflection.md` is stored in the EPD attempt,
and the reflection contains a one-paragraph evidence summary.  For missing
telemetry, assert the Student classification turn runs before `unactivated`;
assert an evidence-supported recommended state is recorded while controller
promotion authority remains unchanged.

- [ ] **Step 2: Run the reflection tests.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_student_reflection_precedes_unactivated_fallback -q`

Expected: failure because no reflection artifact is produced.

- [ ] **Step 3: Implement reflection and bounded classification parsing.**

Add `reflect()` to the Student editor protocol and a Noop implementation.
Persist only fields parsed from the documented response; never allow reflection
text to mark a candidate promoted or bypass 4/4 evidence.  Write the reflection
artifact path into candidate artifacts before EPD recording.

- [ ] **Step 4: Run lifecycle regressions.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core -q`

Expected: lifecycle, telemetry repair, and EPD status tests pass.

### Task 8: Add the observer-only Codex narrator plugin

**Files:**
- Create: `goalevolve/agents/narrator.py`
- Modify: `goalevolve/core/plugins.py`, `goalevolve/config.py`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing authority-boundary tests.**

Assert a narrator receives an evidence bundle and writes a summary artifact;
assert its protocol does not expose source-edit, recipe, verdict or promotion
methods; assert runner failure returns observer metadata without changing the
candidate verdict.

- [ ] **Step 2: Run narrator tests.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_narrator_is_observer_only -q`

Expected: import failure for `CodexNarrativeSummarizer`.

- [ ] **Step 3: Implement narrator protocol and registry.**

Give the plugin a `summarize(state_root, context, artifact_root)` method backed
by `PersistentCodexRunner`; use an identity distinct from Teacher/Student and
write `narrative_summary.md`.  Register it but call it only from an explicit
Controller diagnostic request.

- [ ] **Step 4: Run the full unit suite.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core -q`

Expected: all unit tests pass.

### Task 9: Add bounded no-promotion campaign stopping

**Files:**
- Modify: `goalevolve/config.py`, `goalevolve/execution/engine.py`,
  `goalevolve/cli.py`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing stop-policy tests.**

Create a mock evaluator with three consecutive nonpromoted rounds and request
eight rounds.  Assert `run()` stops after round three and writes the stop reason;
assert a promotion resets the streak.

- [ ] **Step 2: Run the narrow test.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_campaign_stops_after_configured_no_promotion_streak -q`

Expected: failure because current `run()` always executes every requested round.

- [ ] **Step 3: Implement `max_consecutive_no_promotion_rounds`.**

Default to `3` for campaign profiles, preserve `None` as disabled, and persist
the counter and stop reason in campaign metadata.  The CLI `--rounds` remains
the hard upper bound.

- [ ] **Step 4: Run unit and smoke verification.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core -q && PYTHONPATH=. python3 -m goalevolve.cli smoke --state-root /tmp/goalevolve-prompt-smoke --rounds 3 --clean`

Expected: unit tests and the deterministic campaign finish successfully.

### Task 10: Complete verification and real AES P0 campaign

**Files:**
- Create: a local, ignored AES P0 runtime profile outside the repository
- Test: full unit/artifact suites and real campaign artifacts

- [ ] **Step 1: Verify the complete source tree.**

Run: `PYTHONPATH=. python3 -m unittest tests.unit.test_core -q && PYTHONPATH=. pytest tests/artifact -q && git diff --check`

Expected: zero test failures and no whitespace errors.

- [ ] **Step 2: Create and validate an AES P0 profile.**

The profile has no initial parent, points at the frozen OpenROAD P0 source,
uses an isolated state root, sets `max_campaign_rounds: 8` and
`max_consecutive_no_promotion_rounds: 3`, and retains the AES frozen targets.
Run `goalevolve baseline` first and inspect `baseline.json` for all three QoR
metrics and official 4/4 evidence.

- [ ] **Step 3: Run the real campaign and inspect evidence.**

Run: `PYTHONPATH=. python3 -m goalevolve.cli run --config <p0-profile> --rounds 8`

Expected: at most eight rounds, every candidate has controller evidence, EPD
objects, retrieval traces and Student reflection paths; report the best verified
parent versus P0 regardless of whether an improvement occurs.

- [ ] **Step 4: Audit every source-plan chapter.**

Map each numbered heading in `plan_prompt_and_EPD_enhance.md` to code paths,
tests and actual AES artifacts.  Explicitly list a requirement as not
implemented if the real evidence does not prove it.
