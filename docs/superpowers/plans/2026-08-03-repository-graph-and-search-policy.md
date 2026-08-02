# Repository Graph and Search Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an incremental AST repository graph and Doc Cards for OpenROAD `rsz`/`rmp`, inject graph-grounded localization into Teacher planning, and persist an evidence-only search policy without changing official promotion.

**Architecture:** `repository_graph.py` produces source-hash-scoped AST facts, a conservative graph, and direct-source Doc Cards.  The existing assignment materializer consumes the graph for exact anchor admission.  `search_policy.py` reads immutable EPD/round evidence and emits advice for the Teacher, while the existing Parent and promotion implementations remain authoritative.

**Tech Stack:** Python 3.11+, `tree-sitter==0.26.0`, `tree-sitter-cpp==0.23.4`, standard-library JSON and SHA-256, `unittest`.

---

### Task 1: Lock Parser Dependencies and Define Graph Facts

**Files:**
- Modify: `pyproject.toml`
- Create: `goalevolve/planning/repository_graph.py`
- Modify: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing AST extraction tests**

```python
from goalevolve.planning.repository_graph import RepositoryGraphIndex

def test_repository_graph_extracts_qualified_symbols_and_includes(self):
    source = self._write_graph_source_tree()
    graph = RepositoryGraphIndex(state_root=self.root / "state").build(
        source_root=source, source_hash="parent_a", allowed_patch_roots=("src/rsz", "src/rmp"),
    )
    self.assertTrue(graph.symbol_for_anchor("src/rsz/Foo.cc::rsz::Foo::run"))
    self.assertEqual(graph.edges_of_kind("includes"), (("file:src/rsz/Foo.cc", "file:src/rsz/Foo.hh"),))
```

- [ ] **Step 2: Run the test and verify import failure**

Run: `PYTHONPATH=. python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_repository_graph_extracts_qualified_symbols_and_includes -v`

Expected: `ModuleNotFoundError` for `goalevolve.planning.repository_graph`.

- [ ] **Step 3: Implement immutable graph records and tree-sitter traversal**

Implement `RepositoryGraphIndex`, `RepositoryGraph`, file/symbol records,
`build()`, AST error recording, lexical qualified names, includes, source
digests, and atomic `manifest.json`, `graph.json`, `doc_cards.json` output.

- [ ] **Step 4: Lock runtime dependencies**

Add the exact parser dependencies under `[project] dependencies` in
`pyproject.toml` so a clean install has the required binding and grammar.

- [ ] **Step 5: Run the focused test and verify pass**

Run: `PYTHONPATH=. python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_repository_graph_extracts_qualified_symbols_and_includes -v`

Expected: `OK`.

### Task 2: Generate Conservative Calls, Cards, and Incremental Artifacts

**Files:**
- Modify: `goalevolve/planning/repository_graph.py`
- Modify: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing incremental and Doc Card tests**

```python
def test_repository_graph_reuses_unchanged_file_and_reparses_changed_file(self):
    first = self.graph_index.build(source_root=self.source, source_hash="p1", allowed_patch_roots=("src/rsz", "src/rmp"))
    self._write("src/rmp/Bar.cc", "namespace rmp { void Bar::run() {} }\n")
    second = self.graph_index.build(source_root=self.source, source_hash="p2", allowed_patch_roots=("src/rsz", "src/rmp"))
    self.assertIn("src/rsz/Foo.cc", second.reused_files)
    self.assertIn("src/rmp/Bar.cc", second.reparsed_files)

def test_doc_card_exposes_only_source_derived_fields(self):
    graph = self.graph_index.build(source_root=self.source, source_hash="p1", allowed_patch_roots=("src/rsz", "src/rmp"))
    card = graph.doc_card("src/rsz/Foo.cc::rsz::Foo::run")
    self.assertEqual(card["path"], "src/rsz/Foo.cc")
    self.assertIn("signature", card)
    self.assertNotIn("recommendation", card)
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `PYTHONPATH=. python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_repository_graph_reuses_unchanged_file_and_reparses_changed_file tests.unit.test_core.GoalEvolveV2Tests.test_doc_card_exposes_only_source_derived_fields -v`

Expected: failure because reuse metadata and Doc Card API do not exist.

- [ ] **Step 3: Implement cache reuse, direct call resolution, and cards**

Reuse matching relative-path/digest records from the latest compatible manifest;
parse only added/changed files; remove deleted paths; recompute calls by uniquely
matching unqualified or qualified call names.  Add deterministic card and bounded
`focus()` APIs.  Do not infer external or ambiguous call edges.

- [ ] **Step 4: Run the focused tests and verify pass**

Run: the command from Step 2.

Expected: `OK`.

### Task 3: Replace Regex Admission with AST Anchor Admission

**Files:**
- Modify: `goalevolve/execution/teacher_assignment.py`
- Modify: `goalevolve/execution/engine.py`
- Modify: `goalevolve/agents/teacher.py`
- Modify: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing graph-anchor tests**

```python
def test_controller_rejects_ambiguous_short_ast_anchor(self):
    result = self._materialize_graph_assignment(
        source_evidence=("src/rsz/Foo.cc::run",),
        repository_graph=self.graph,
    )
    self.assertIn("ambiguous_source_symbol:src/rsz/Foo.cc::run", result.errors)

def test_controller_accepts_unique_qualified_ast_anchor(self):
    result = self._materialize_graph_assignment(
        source_evidence=("src/rsz/Foo.cc::rsz::Foo::run",),
        repository_graph=self.graph,
    )
    self.assertFalse(result.errors)
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `PYTHONPATH=. python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_controller_rejects_ambiguous_short_ast_anchor tests.unit.test_core.GoalEvolveV2Tests.test_controller_accepts_unique_qualified_ast_anchor -v`

Expected: failure because materialization has no graph parameter.

- [ ] **Step 3: Inject parent graph into Teacher plan and materialization**

Build the parent graph once per Codex Teacher round, pass its compact view to the
prompt, persist its manifest location in `teacher_plan.json`, and require every
hook evidence entry to resolve through the graph.  Preserve the public
`source_structure_index()` function as a graph-backed compatibility view.

- [ ] **Step 4: Run focused and existing Teacher tests**

Run: `PYTHONPATH=. python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_controller_rejects_ambiguous_short_ast_anchor tests.unit.test_core.GoalEvolveV2Tests.test_controller_accepts_unique_qualified_ast_anchor tests.unit.test_core.GoalEvolveV2Tests.test_codex_engine_uses_teacher_authored_mechanisms_not_planner_cards -v`

Expected: `OK`.

### Task 4: Add Evidence-Only Hill-Climb Search Policy

**Files:**
- Create: `goalevolve/planning/search_policy.py`
- Modify: `goalevolve/execution/engine.py`
- Modify: `goalevolve/agents/teacher.py`
- Modify: `tests/unit/test_core.py`

- [ ] **Step 1: Write failing policy tests**

```python
from goalevolve.planning.search_policy import SearchPolicyBuilder

def test_search_policy_keeps_current_parent_as_only_incumbent(self):
    policy = SearchPolicyBuilder(self.root / "state").build(parent=self.parent, diagnosis=self.diagnosis, epd_portfolio=self.portfolio, repository_graph=self.graph)
    self.assertEqual(policy["hill_climb"]["incumbent_parent_id"], self.parent.parent_id)
    self.assertEqual(policy["hill_climb"]["alternative_parent_ids"], [])

def test_search_policy_diversifies_after_completed_no_promotion_rounds(self):
    self._write_unpromoted_rounds(2)
    policy = SearchPolicyBuilder(self.root / "state").build(parent=self.parent, diagnosis=self.diagnosis, epd_portfolio=self.portfolio, repository_graph=self.graph)
    self.assertTrue(policy["diversification"]["required"])
```

- [ ] **Step 2: Run focused policy tests and verify failure**

Run: `PYTHONPATH=. python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_search_policy_keeps_current_parent_as_only_incumbent tests.unit.test_core.GoalEvolveV2Tests.test_search_policy_diversifies_after_completed_no_promotion_rounds -v`

Expected: `ModuleNotFoundError` for `search_policy`.

- [ ] **Step 3: Implement policy construction and Teacher injection**

Derive no-promotion streak from completed round summaries, collect validated EPD
elite record IDs without changing their eligibility, rank graph cards from
diagnostic/EPD source evidence, persist `search_policy.json`, and pass it to the
Teacher as advisory context.  Do not change `PromotionPolicy.choose()` or role
construction.

- [ ] **Step 4: Run focused policy and promotion regression tests**

Run: `PYTHONPATH=. python -m unittest tests.unit.test_core.GoalEvolveV2Tests.test_search_policy_keeps_current_parent_as_only_incumbent tests.unit.test_core.GoalEvolveV2Tests.test_search_policy_diversifies_after_completed_no_promotion_rounds tests.unit.test_core.GoalEvolveV2Tests.test_four_of_four_gate_and_mechanism_attribution -v`

Expected: `OK`.

### Task 5: Documentation, Full Regression, and Completion Audit

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `goalevolve/README.md`
- Modify: `goalevolve/README.zh-CN.md`
- Modify: `tests/unit/test_core.py`

- [ ] **Step 1: Add concise documented artifacts and dependency setup**

Document the source-hash graph artifact, supported `rsz`/`rmp` scope, expected
Doc Card contents, and that graph/search policy do not replace full-flow
evaluation or official promotion.

- [ ] **Step 2: Run graph-focused test set**

Run: `PYTHONPATH=. python -m unittest tests.unit.test_core -v`

Expected: all tests pass.

- [ ] **Step 3: Run the complete Python test suite**

Run: `PYTHONPATH=. python -m unittest discover -s tests/unit -v`

Expected: all tests pass with zero failures.

- [ ] **Step 4: Run artifact static checks without a full flow**

Run: `PYTHONPATH=. python -m unittest discover -s tests/artifact -v`

Expected: all static artifact tests pass; no new OpenROAD campaign is run.

- [ ] **Step 5: Inspect change scope and commit**

Run: `git diff --check && git status --short`

Expected: only graph, policy, tests, dependency, and documentation changes.
Commit with `feat: add AST repository graph and evidence search policy`.
