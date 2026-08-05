# AES Power-Regression Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore an AST-assisted AES P0 campaign whose first ten official rounds contain at least five real parent promotions.

**Architecture:** A ready P0 profile declares the exact power-reclaim settings and the controller rejects any effective evaluator settings that differ. The graph remains a source-anchor validator, while the Teacher receives a compact execution witness plus graph-resolvable v2 mechanism summaries which are explicitly forbidden from supplying a parent or QoR evidence. Existing official evaluation and promotion policies are unchanged.

**Tech Stack:** Python, dataclasses, JSON profiles, unittest, existing Codex/OpenROAD integration.

---

### Task 1: Declare and validate the effective power profile

**Files:**
- Modify: `goalevolve/config.py:80-205`
- Modify: `goalevolve/evaluation/contest2026.py:56-95,600-630`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_ready_profile_rejects_power_reclaim_declaration_mismatch(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "profile.json"
        path.write_text(json.dumps({
            "design": "aes_cipher_top", "campaign_ready": True,
            "baseline_metrics": {"tns_abs_ns": 12.69}, "target_metrics": {"tns_abs_ns": 12.0},
            "power_reclaim_proportion_percent": 1.0, "power_reclaim_max_moves": 1,
            "declared_power_reclaim_profile": {"phase": "early_forced_reclaim", "proportion_percent": 80.0, "max_moves": 0},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "declared power-reclaim profile differs"):
            load_config(path)

def test_contest_profile_is_the_single_tcl_source(self) -> None:
    evaluator = Contest2026OpenROADEvaluator(Contest2026Config(
        design="aes_cipher_top", benchmark_root=Path("/bench"), source_seed=Path("/source"),
        power_reclaim_proportion_percent=80.0, power_reclaim_max_moves=0,
    ))
    self.assertEqual(evaluator.effective_power_reclaim_profile(), {
        "phase": "early_forced_reclaim", "proportion_percent": 80.0, "max_moves": 0,
    })
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_ready_profile_rejects_power_reclaim_declaration_mismatch tests.unit.test_core.GoalEvolveV2Tests.test_contest_profile_is_the_single_tcl_source -v`

Expected: FAIL because neither declared profile validation nor evaluator profile accessor exists.

- [ ] **Step 3: Implement only the needed profile model**

```python
@dataclass(frozen=True)
class PowerReclaimProfile:
    phase: str
    proportion_percent: float
    max_moves: int
    def to_dict(self) -> dict[str, object]:
        return {"phase": self.phase, "proportion_percent": self.proportion_percent, "max_moves": self.max_moves}
```

`load_config()` parses `declared_power_reclaim_profile`; a ready contest profile requires it and raises when it differs from the three resolved fields. `Contest2026OpenROADEvaluator.effective_power_reclaim_profile()` returns that same three-key shape and `baseline_identity()` uses it rather than duplicating fields.

- [ ] **Step 4: Verify GREEN**

Run: same command as Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add goalevolve/config.py goalevolve/evaluation/contest2026.py tests/unit/test_core.py && git commit -m "feat: verify declared power-reclaim profile"`

### Task 2: Audit the profile before campaign initialization

**Files:**
- Modify: `goalevolve/cli.py:18-135`
- Modify: `goalevolve/execution/engine.py:135-210`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_command_run_writes_verified_execution_profile_before_rounds(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        state_root = Path(temporary)
        profile = _verify_execution_profile(
            config=SimpleNamespace(declared_power_reclaim_profile=PowerReclaimProfile("early_forced_reclaim", 80.0, 0)),
            evaluator=SimpleNamespace(effective_power_reclaim_profile=lambda: {"phase": "early_forced_reclaim", "proportion_percent": 80.0, "max_moves": 0}),
            state_root=state_root,
        )
        self.assertEqual(profile["declared"], profile["effective"])
        self.assertEqual(load_json(state_root / "execution_profile.json")["decision_role"], "execution_audit_only")

def test_command_run_rejects_effective_profile_drift_before_initialize(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        state_root = Path(temporary)
        with self.assertRaisesRegex(RuntimeError, "effective power-reclaim profile differs"):
            _verify_execution_profile(
                config=SimpleNamespace(declared_power_reclaim_profile=PowerReclaimProfile("early_forced_reclaim", 80.0, 0)),
                evaluator=SimpleNamespace(effective_power_reclaim_profile=lambda: {"phase": "early_forced_reclaim", "proportion_percent": 1.0, "max_moves": 1}),
                state_root=state_root,
            )
        self.assertFalse((state_root / "contract.json").exists())
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_command_run_writes_verified_execution_profile_before_rounds tests.unit.test_core.GoalEvolveV2Tests.test_command_run_rejects_effective_profile_drift_before_initialize -v`

Expected: FAIL because no audit exists and initialization is not guarded.

- [ ] **Step 3: Implement launch guard**

```python
def _verify_execution_profile(*, config, evaluator, state_root: Path) -> dict[str, object]:
    declared = config.declared_power_reclaim_profile.to_dict()
    effective = evaluator.effective_power_reclaim_profile()
    if effective != declared:
        raise RuntimeError("effective power-reclaim profile differs from declared profile")
    payload = {"schema_version": "goalevolve.execution-profile.v1", "decision_role": "execution_audit_only", "declared": declared, "effective": effective}
    atomic_json(state_root / "execution_profile.json", payload)
    return payload
```

Call this after readiness validation and before `engine.initialize()`. Add only the profile digest to runtime provenance; do not change `contract.json`, evaluator Tcl, or promotion inputs.

- [ ] **Step 4: Verify GREEN and resume compatibility**

Run: `python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_command_run_writes_verified_execution_profile_before_rounds tests.unit.test_core.GoalEvolveV2Tests.test_command_run_rejects_effective_profile_drift_before_initialize tests.unit.test_core.GoalEvolveV2Tests.test_campaign_resume_allows_provenance_change_but_not_qor_contract_change -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add goalevolve/cli.py goalevolve/execution/engine.py tests/unit/test_core.py && git commit -m "feat: audit campaign execution profile"`

### Task 3: Keep graph localization additive and transport revalidation-only seeds

**Files:**
- Create: `goalevolve/planning/historical_seeds.py`
- Modify: `goalevolve/config.py`, `goalevolve/execution/engine.py:630-720`
- Modify: `goalevolve/agents/teacher.py:110-160,665-725`
- Modify: `goalevolve/agents/teacher_packet.py:35-105,330-390`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_teacher_packet_marks_historical_seed_as_revalidation_only(self) -> None:
    prompt = CodexTeacher._plan_prompt(
        parent=self.parent, diagnosis=SimpleNamespace(to_dict=lambda: {}), epd={}, observations={},
        previous_review={}, fallback=(replace(self.hypothesis, student_id="student_1"),), historical_seeds=(
        {"seed_id": "v2_r3", "source_hooks": ["src/rsz/src/RepairPowerPolicy.cc"], "summary": "bounded VT selection"},
    ))
    self.assertIn("## Historical Mechanism Seeds (revalidation only)", prompt)
    self.assertIn("cannot supply a parent, QoR metric, or promotion", prompt)

def test_graph_filter_excludes_seed_with_unresolved_anchor(self) -> None:
    self.assertEqual(graph_resolvable_seeds(graph, ({"source_hooks": ["src/missing.cc"]},)), ())
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_teacher_packet_marks_historical_seed_as_revalidation_only tests.unit.test_core.GoalEvolveV2Tests.test_graph_filter_excludes_seed_with_unresolved_anchor -v`

Expected: FAIL because no seed transport/filter is present.

- [ ] **Step 3: Implement source-bound seed handling**

```python
def graph_resolvable_seeds(graph, seeds):
    return tuple(seed for seed in seeds if all(graph.resolve_anchor(anchor) is not None for anchor in seed["source_hooks"]))
```

Accept only `seed_id`, `source_hooks`, `decision_boundary`, `summary`, and `expected_signals`; reject `metrics`, `parent_id`, `source_commit`, and `promotion`. Pass only graph-resolvable seeds to the Teacher. Add packet language: the focus slice locates likely paths but does not forbid a live graph-resolvable off-slice hook when the Teacher records a distinct boundary and falsification condition.

- [ ] **Step 4: Verify GREEN and graph regression tests**

Run: `python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_teacher_packet_marks_historical_seed_as_revalidation_only tests.unit.test_core.GoalEvolveV2Tests.test_graph_filter_excludes_seed_with_unresolved_anchor tests.unit.test_core.GoalEvolveV2Tests.test_teacher_prompt_includes_p0_rooted_doc_card_packet tests.unit.test_core.GoalEvolveV2Tests.test_repository_graph_focus_filters_cards_to_allowed_patch_roots -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add goalevolve/planning/historical_seeds.py goalevolve/config.py goalevolve/execution/engine.py goalevolve/agents/teacher.py goalevolve/agents/teacher_packet.py tests/unit/test_core.py && git commit -m "feat: retain graph-resolvable historical mechanism seeds"`

### Task 4: Add reproducible fresh AES evidence inputs

**Files:**
- Create: `experiments/aes_cipher_top/aes_v2_seed_cards.json`
- Create: `experiments/aes_cipher_top/p0_recovery_20260805.local.json`
- Test: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing profile test**

```python
def test_aes_recovery_profile_is_fresh_and_has_unlimited_reclaim(self) -> None:
    config = load_config(PROJECT_ROOT / "experiments/aes_cipher_top/p0_recovery_20260805.local.json")
    self.assertTrue(config.campaign_ready)
    self.assertEqual(config.power_reclaim_proportion_percent, 80.0)
    self.assertEqual(config.power_reclaim_max_moves, 0)
    self.assertIsNone(config.initial_parent_id)
    self.assertNotIn("supervision_20260805/aes_cipher_top/campaign", str(config.state_root))
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_aes_recovery_profile_is_fresh_and_has_unlimited_reclaim -v`

Expected: FAIL because recovery profile is absent.

- [ ] **Step 3: Create the profile and three seed cards**

Set fresh state root, P0 source, no initial parent, `power_first_promotion`, 60ns ceiling, ten-round limit, and exact declared/effective `early_forced_reclaim/80.0/0`. Seed JSON contains only Task 3 allowed fields and v2 R3/R4 source-boundary summaries, never historical QoR or parent IDs.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_aes_recovery_profile_is_fresh_and_has_unlimited_reclaim -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add experiments/aes_cipher_top/aes_v2_seed_cards.json experiments/aes_cipher_top/p0_recovery_20260805.local.json tests/unit/test_core.py && git commit -m "feat: add reproducible AES recovery profile"`

### Task 5: Verify software and prove the actual campaign

**Files:**
- Verify: all prior modified files
- Produce: fresh state root declared in `p0_recovery_20260805.local.json`

- [ ] **Step 1: Run the relevant unit suites**

Run: `python -m unittest tests.unit.test_core tests.unit.test_dashboard tests.unit.test_epd_search -v`

Expected: PASS except an already documented credential-symlink portability failure unrelated to these changes.

- [ ] **Step 2: Check source hygiene**

Run: `git diff --check`

Expected: no output.

- [ ] **Step 3: Run fresh official P0 baseline and ten rounds**

Run: `python -m goalevolve.cli baseline --config experiments/aes_cipher_top/p0_recovery_20260805.local.json`

Run: `python -m goalevolve.cli run --config experiments/aes_cipher_top/p0_recovery_20260805.local.json --rounds 10`

Expected: `execution_profile.json` shows 80.0/0; no imported parent is present; all promotion evidence is official.

- [ ] **Step 4: Audit the acceptance condition**

Run: `jq -s '[.[] | select(.promoted_student != null)] | length' <(for r in outputs/aes_recovery_20260805/aes_cipher_top/campaign/rounds/round_*/round.json; do jq '{promoted_student: .promoted_student}' "$r"; done)`

Expected: `5` or higher. For every promoted round inspect `candidate.json`, `evidence.json`, official 4/4 log, zero `drv_count`, and strict Controller verdict. If the count is lower, preserve the entire run, use its evidence to make one further TDD-backed causal correction, and rerun from a distinct fresh P0 root; never manually promote.

- [ ] **Step 5: Commit verified implementation and report**

Run: `git add goalevolve experiments/aes_cipher_top tests docs/superpowers && git commit -m "feat: recover AES power-evolution guidance"`
