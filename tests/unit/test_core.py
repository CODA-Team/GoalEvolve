from __future__ import annotations

import inspect
import json
import os
import tempfile
import threading
import time
import unittest
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from goalevolve.config import DEFAULT_BENCHMARK_ROOT, DEFAULT_CREDENTIAL_ENV, DEFAULT_OPENROAD_SEED, load_config
from goalevolve.cli import _attach_configured_baseline
from goalevolve.agents.codex_student import CodexStudentConfig, CodexStudentEditor, NoopStudentEditor, StudentEditReport
from goalevolve.agents.codex_runtime import CodexRuntimeConfig, PersistentCodexRunner
from goalevolve.evaluation.contest2026 import Contest2026Config, Contest2026OpenROADEvaluator, _liberty_cell_blocks, _observed_phase_signals, _placement_legal, _power_timing_cell_tradeoff, _read_metrics, _source_diff, _write_rmp_combined_liberty, official_four_check
from goalevolve.evaluation.contest2026 import _checkpoint_metrics
from goalevolve.core.contracts import build_contract
from goalevolve.execution.execution import ExecutionPolicy, ResilientCommandRunner
from goalevolve.planning.epd import EPD_STATUSES, EvolutionProgramDatabase
from goalevolve.execution.engine import GoalEvolveEngine
from goalevolve.testing.evaluators import MockEvaluator
from goalevolve.evaluation.evidence import classify_candidate
from goalevolve.core.io import atomic_json, load_json
from goalevolve.legacy import LegacyImporter
from goalevolve.core.models import CandidateResult, CheckResult, EvidenceVerdict, Hypothesis, Parent
from goalevolve.planning.observations import ObservationMemory
from goalevolve.execution.preflight import preflight_candidate
from goalevolve.planning.retrieval import DiversePlanner, DiverseRetriever, MechanismCard
from goalevolve.planning.scope import SourceScopeResolver
from goalevolve.evaluation.promotion import PowerFirstPromotion, StrictEvidencePromotion
from goalevolve.evaluation.sfinal import score_sfinal
from goalevolve.token_ledger import record_round_token_usage
from goalevolve.agents.teacher import CodexTeacher
from goalevolve.execution.workspace import IsolatedWorkspace
from goalevolve.evaluation.leaderboard import collect_campaign_results, rank_top_results, update_unified_leaderboard


class GoalEvolveV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = build_contract(
            design="unit",
            baseline_metrics={"tns_abs_ns": 100.0, "leakage_power_pw": 200.0},
            target_metrics={"tns_abs_ns": 50.0, "leakage_power_pw": 160.0},
            weights={"tns_abs_ns": 1.0, "leakage_power_pw": 1.0},
        )
        distance, _, _ = self.contract.evaluate(self.contract.baseline_metrics)
        self.parent = Parent("baseline", self.contract.baseline_metrics, "base", "hash", distance)
        self.hypothesis = Hypothesis("h", "ranking", "claim", ("src/rsz/src/RecoverPower.cc",), ("accepted",), ("card",), "ranking:recover")

    def test_missing_metric_cannot_be_compensated(self) -> None:
        distance, residuals, missing = self.contract.evaluate({"tns_abs_ns": 1.0})
        self.assertGreater(distance, 0.0)
        self.assertEqual(missing, ["leakage_power_pw"])
        self.assertIsNone(residuals["leakage_power_pw"])

    def test_goal_contract_rejects_mismatched_decision_metric_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "goal metric names must match"):
            build_contract(
                design="mismatched",
                baseline_metrics={"tns_abs_ns": 100.0},
                target_metrics={"dynamic_power_pw": 50.0},
            )

    def test_campaign_resume_allows_provenance_change_but_not_qor_contract_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            common = {
                "design": "resume",
                "baseline_metrics": {"tns_abs_ns": 100.0},
                "target_metrics": {"tns_abs_ns": 50.0},
            }
            first = build_contract(
                **common,
                source_fingerprint={"source_git_commit": "framework_before"},
            )
            resumed = build_contract(
                **common,
                source_fingerprint={"source_git_commit": "framework_after"},
            )
            engine = GoalEvolveEngine(
                first, root, DiversePlanner(), MockEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion()
            )
            engine.initialize(baseline_metrics=dict(first.baseline_metrics))
            resumed_engine = GoalEvolveEngine(
                resumed, root, DiversePlanner(), MockEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion()
            )
            resumed_engine.initialize(baseline_metrics=dict(resumed.baseline_metrics))
            history = load_json(root / "runtime_provenance.json")
            changed_target = build_contract(
                design="resume",
                baseline_metrics={"tns_abs_ns": 100.0},
                target_metrics={"tns_abs_ns": 40.0},
                source_fingerprint={"source_git_commit": "framework_after"},
            )
            changed_engine = GoalEvolveEngine(
                changed_target, root, DiversePlanner(), MockEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion()
            )
            with self.assertRaisesRegex(RuntimeError, "different frozen goal contract"):
                changed_engine.initialize(baseline_metrics=dict(changed_target.baseline_metrics))

            self.assertEqual(len(history["entries"]), 2)

    def test_epd_baseline_is_not_counted_as_pending_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            epd = EvolutionProgramDatabase(Path(temporary))
            epd.ensure_baseline(self.parent)
            summary = epd.summary()
            teacher = epd.teacher_summary()
            self.assertEqual(summary["status_counts"]["pending"], 0)
            self.assertEqual(summary["record_count"], 0)
            self.assertEqual(summary["baseline_record_count"], 1)
            self.assertEqual(teacher["status_counts"]["pending"], 0)
            self.assertEqual(teacher["decision_records"], [])
            self.assertEqual(teacher["baseline_record_count"], 1)

    def test_sfinal_is_a_pure_observer_formula(self) -> None:
        baseline = {"tns": -10.0, "total_power": 100.0, "leakage_power": 20.0, "tool_runtime": 10.0, "flow_runtime": 20.0, "slew_over_sum": 0.0, "cap_over_sum": 0.0, "fanout_over_sum": 0.0, "max_gr_overflow": None, "total_gr_overflow": None}
        candidate = {"tns": -5.0, "total_power": 80.0, "leakage_power": 15.0, "tool_runtime": 10.0, "flow_runtime": 20.0, "slew_over_sum": 0.0, "cap_over_sum": 0.0, "fanout_over_sum": 0.0, "max_gr_overflow": None, "total_gr_overflow": None}
        score = score_sfinal(baseline=baseline, candidate=candidate, average_displacement=0.0)
        self.assertAlmostEqual(score["TNS_norm"], 0.5)
        self.assertAlmostEqual(score["DPOWER_norm"], 0.1875)
        self.assertAlmostEqual(score["LPOWER_norm"], 0.25)
        self.assertAlmostEqual(score["Sfinal"], 36.875)

    def test_runtime_and_contest_scores_cannot_enter_goal_contract(self) -> None:
        contract = build_contract(
            design="observer_isolation",
            baseline_metrics={
                "tns_abs_ns": 100.0,
                "runtime_s": 60.0,
                "SPPA": 10.0,
                "Sfinal": 5.0,
            },
            target_metrics={
                "tns_abs_ns": 50.0,
                "runtime_s": 30.0,
                "SPPA": 20.0,
                "Sfinal": 10.0,
            },
        )
        self.assertEqual([spec.name for spec in contract.metrics], ["tns_abs_ns"])
        self.assertEqual(contract.baseline_metrics, {"tns_abs_ns": 100.0})
        distance, residuals, missing = contract.evaluate(
            {"tns_abs_ns": 60.0, "runtime_s": 1.0, "SPPA": 100.0, "Sfinal": 100.0}
        )
        self.assertGreater(distance, 0.0)
        self.assertEqual(set(residuals), {"tns_abs_ns"})
        self.assertEqual(missing, [])

    def test_leaderboard_ranks_verified_results_by_frozen_distance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = root / "campaign_a"
            atomic_json(campaign / "contract.json", self.contract.to_dict())
            atomic_json(campaign / "parent.json", {"parent_id": "round_002:student_1"})
            for round_index, tns, leakage, evidence in (
                (1, 70.0, 180.0, "validated"),
                (2, 60.0, 170.0, "refuted"),
                (3, 60.0, 170.0, "validated"),
            ):
                artifacts = campaign / "rounds" / f"round_{round_index:03d}" / "students" / "student_1" / "artifacts"
                contest_output = artifacts / "contest_output"
                contest_output.mkdir(parents=True, exist_ok=True)
                (contest_output / "evaluate.tcl").write_text(f"# round {round_index}\n", encoding="utf-8")
                (contest_output / "evaluation.log").write_text("OpenROAD completed\n", encoding="utf-8")
                atomic_json(artifacts / "candidate.json", {
                    "student_id": "student_1",
                    "metrics": {"tns_abs_ns": tns, "leakage_power_pw": leakage, "drv_count": 0.0, "runtime_s": 10.0},
                    "checks": [{"name": name, "passed": True} for name in ("build", "flow", "metrics", "lec")],
                    "evaluation_error": None,
                    "hypothesis": {"timing_recipe_id": "unit_recipe"},
                    "source_commit": f"commit-{round_index}",
                    "artifacts": {"result_root": str(root / f"external_result_{round_index}")},
                })
                atomic_json(artifacts / "evidence.json", {"state": evidence})
            rows = collect_campaign_results(campaign)
            ranked = rank_top_results(rows, top_k=5)
            self.assertEqual(len(ranked), 2)
            self.assertEqual(ranked[0]["result_id"], "round_002:student_1")
            self.assertEqual(ranked[0]["lineage_status"], "current_parent")
            self.assertEqual(ranked[0]["artifact_dir"], str(root / "external_result_2"))
            self.assertEqual(ranked[0]["tcl_file"], str((campaign / "rounds/round_002/students/student_1/artifacts/contest_output/evaluate.tcl").resolve()))
            self.assertEqual(len(ranked[0]["tcl_sha256"]), 64)
            self.assertTrue(ranked[0]["execution_log"].endswith("evaluation.log"))
            report = update_unified_leaderboard(leaderboard_root=root / "leaderboard", state_roots=(campaign,))
            self.assertEqual(report["decision_role"], "observer_only")
            self.assertTrue((root / "leaderboard" / "leaderboard_top5.csv").is_file())
            self.assertTrue((root / "leaderboard" / "leaderboard_top5.md").is_file())

    def test_placement_guard_accepts_normal_detailed_placement_info(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "evaluation.log"
            log.write_text(
                "[INFO DPL-0006] Core area: 2565.60 um^2\n"
                "[INFO DPL-0005] Diamond search max displacement: +/- 500 sites\n"
                "[INFO DPL-1101] Legalizing using diamond search.\n"
                "Placement legalized.\n",
                encoding="utf-8",
            )
            self.assertEqual(_placement_legal(log), (True, "post_detailed_placement"))

    def test_placement_guard_rejects_error_level_checker_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "evaluation.log"
            log.write_text(
                "Placement legalized.\n"
                "[ERROR DPL-0033] detailed placement checks failed during check placement.\n",
                encoding="utf-8",
            )
            passed, detail = _placement_legal(log)
            self.assertFalse(passed)
            self.assertEqual(detail, "placement_violation:[ERROR DPL-0033]")

    def test_teacher_prompts_mark_runtime_as_observer_only(self) -> None:
        from goalevolve.agents.teacher import CodexTeacher

        diagnosis = SimpleNamespace(to_dict=lambda: {})
        plan = CodexTeacher._plan_prompt(parent=self.parent, diagnosis=diagnosis, epd={}, observations={}, previous_review={}, fallback=(self.hypothesis,))
        review = CodexTeacher._review_prompt(parent=self.parent, diagnosis=diagnosis, epd={}, observations={}, rows=())
        self.assertIn("Runtime is execution telemetry only", plan)
        self.assertIn("Runtime is execution telemetry only", review)
        self.assertIn("Return Markdown field blocks", plan)
        self.assertIn("Return Markdown field blocks", review)
        self.assertNotIn("Return exactly one JSON object", plan)
        self.assertNotIn("Return exactly one JSON object", review)

    def test_teacher_markdown_protocol_parses_fixed_assignment_and_review_blocks(self) -> None:
        from goalevolve.agents.markdown_protocol import parse_teacher_plan, parse_teacher_review

        plan = parse_teacher_plan(
            """## Diagnosis Summary
Timing is the dominant residual.

## Evolution Ideas
- Add one bounded endpoint-ranking guard.
- Preserve the validated power mechanism while recovering timing.

## Parent Policy
Keep the current checked parent.

## Student Assignments
### student_1
- Role: explorer
- Candidate: r2_student_1_timing_guard
- Claim: Explore a bounded timing frontier.
- Selection Rationale: It directly addresses the current timing debt.
- Source Hooks: `src/rsz/src/Timing.cc`
- Expected Signals: `timing_examined`
- Falsification Condition: No official QoR gain.
- EPD References: none

### student_3
- Role: integrator
- Claim: Port the compatible portions of two verified mechanisms.
- Source Hooks: `src/rsz/src/Timing.cc`, `src/rsz/src/Power.cc`
- Expected Signals: `timing_examined`, `power_retained`
- Falsification Condition: The combination is not buildable or regresses the contract.
- EPD References: `EPD_timing`, `EPD_power`
"""
        )
        review = parse_teacher_review(
            """## Round Assessment
One timing mechanism is validated.

## Mechanism Actions
### timing_family
- Action: refine
- Evidence Classification: validated_official_gain
- Rationale: Improve the bounded candidate ordering.

## Next Round Constraints
Retain the verified timing guard.
"""
        )
        self.assertEqual(plan["diagnosis_summary"], "Timing is the dominant residual.")
        self.assertEqual(
            plan["evolution_ideas"],
            (
                "Add one bounded endpoint-ranking guard.",
                "Preserve the validated power mechanism while recovering timing.",
            ),
        )
        self.assertEqual(plan["assignments"][0]["student_id"], "student_1")
        self.assertEqual(plan["assignments"][0]["candidate_id"], "r2_student_1_timing_guard")
        self.assertEqual(
            plan["assignments"][0]["selection_rationale"],
            "It directly addresses the current timing debt.",
        )
        self.assertEqual(plan["assignments"][1]["epd_record_ids"], ("EPD_timing", "EPD_power"))
        self.assertEqual(review["mechanism_actions"][0]["action"], "refine")
        self.assertEqual(review["next_round_constraints"], "Retain the verified timing guard.")

    def test_teacher_markdown_protocol_preserves_overload_declarator_commas(self) -> None:
        from goalevolve.agents.markdown_protocol import parse_teacher_plan

        parsed = parse_teacher_plan(
            """## Student Assignments
### student_1
- Role: explorer
- Source Evidence: src/rsz/Foo.cc::rsz::Foo::run(int count, double ratio); src/rmp/Bar.cpp::rmp::Bar::run()

### student_2
- Role: explorer
- Source Evidence: src/rsz/Foo.cc::rsz::Foo::run(int count, double ratio), src/rmp/Bar.cpp::rmp::Bar::run()
"""
        )
        expected = (
            "src/rsz/Foo.cc::rsz::Foo::run(int count, double ratio)",
            "src/rmp/Bar.cpp::rmp::Bar::run()",
        )
        self.assertEqual(parsed["assignments"][0]["source_evidence"], expected)
        self.assertEqual(parsed["assignments"][1]["source_evidence"], expected)

    def test_teacher_markdown_protocol_preserves_structured_pending_idea_fields(self) -> None:
        from goalevolve.agents.markdown_protocol import parse_teacher_plan

        parsed = parse_teacher_plan(
            """## Diagnosis Summary
Timing remains the active residual.

## Evolution Ideas
### idea_1
- Idea: Admit one bounded endpoint guard.
- Predicted Stage Effect: Reduce post-repair timing debt.
- Source Hooks: src/rsz/src/Timing.cc
- Expected Signals: endpoint_guard_examined
- Priority: 3

## Parent Policy
Keep the checked parent.

## Student Assignments
### student_1
- Role: explorer
- Candidate: candidate_1
- EPD Idea: idea_1
- Claim: Admit one bounded endpoint guard.
- Selection Rationale: Highest current timing value.
- Source Hooks: src/rsz/src/Timing.cc
- Expected Signals: endpoint_guard_examined
- Falsification Condition: No official gain.
- EPD References: none
"""
        )

        idea = parsed["evolution_idea_records"][0]
        self.assertEqual(idea["reference"], "idea_1")
        self.assertEqual(idea["predicted_stage_effect"], "Reduce post-repair timing debt.")
        self.assertEqual(idea["source_hooks"], ("src/rsz/src/Timing.cc",))
        self.assertEqual(idea["expected_signals"], ("endpoint_guard_examined",))
        self.assertEqual(parsed["assignments"][0]["idea_reference"], "idea_1")

    def test_teacher_plan_validation_requires_five_explorer_ideas_and_four_role_blocks(self) -> None:
        from goalevolve.agents.markdown_protocol import teacher_plan_validation_errors

        malformed = """## Diagnosis Summary
Timing is constrained.

## Evolution Ideas
### idea_1
- Idea: One idea only.

## Parent Policy
Retain parent.

## Student Assignments
### student_1
- Role: explorer
"""
        errors = teacher_plan_validation_errors(
            malformed,
            required_roles=("explorer", "explorer", "integrator", "enhancer"),
            require_explorer_ideas=True,
        )
        self.assertIn("explorer_idea_count:1<5", errors)
        self.assertIn("missing_assignment:student_2", errors)
        self.assertIn("missing_assignment:student_3", errors)
        self.assertIn("missing_assignment:student_4", errors)

    def test_teacher_plan_parses_explorer_novelty_evidence(self) -> None:
        from goalevolve.agents.markdown_protocol import parse_teacher_plan

        parsed = parse_teacher_plan(
            """## Evolution Ideas
### idea_1
- Idea: Bound a late candidate by measured post-route debt.
- Draft Signature: draft_1
- EPD Search Query: draft_1
- Retrieved Historical Ideas: IDEA_A, IDEA_B
- Opened EPD Records: IDEA_A, IDEA_B
- Nearest Historical Idea: IDEA_A
- Semantic Overlap: Both alter candidate admission.
- Material Difference: This reads post-route debt rather than raw slack.
- Novelty Conclusion: Retain because the observed state is materially different.

## Parent Policy
Keep parent.

## Student Assignments
"""
        )
        idea = parsed["evolution_idea_records"][0]
        self.assertEqual(idea["draft_signature_id"], "draft_1")
        self.assertEqual(idea["retrieved_historical_ideas"], ("IDEA_A", "IDEA_B"))
        self.assertEqual(idea["nearest_historical_idea"], "IDEA_A")
        self.assertIn("post-route debt", idea["material_difference"])

    def test_codex_teacher_uses_draft_retrieval_then_novelty_review(self) -> None:
        from goalevolve.agents.teacher import CodexTeacher, CodexTeacherConfig

        def draft_markdown() -> str:
            rows = ["## Draft Mechanism Signatures"]
            for index in range(1, 6):
                rows.extend(
                    [
                        f"### draft_{index}",
                        "- Stage: timing_recovery",
                        f"- Problem: endpoint timing debt {index}",
                        "- Source Hook: src/rsz/src/Timing.cc",
                        "- Decision Type: candidate admission",
                        "- Observed State: endpoint slack",
                        "- Action: bound one candidate",
                        "- Guard: official timing evidence",
                        "- Expected Effect: reduce timing debt",
                        "",
                    ]
                )
            return "\n".join(rows)

        def final_markdown() -> str:
            rows = [
                "## Diagnosis Summary",
                "Timing debt remains at the endpoint admission boundary.",
                "",
                "## Source Investigation",
                "### investigation_1",
                "- Source Evidence: src/rsz/src/Timing.cc::adjustTiming",
                "- Observed Control Point: The current parent ranks endpoint candidates here.",
                "### investigation_2",
                "- Source Evidence: src/rsz/src/Timing.cc::adjustTiming",
                "- Observed Control Point: The same hook applies the final bounded admission decision.",
                "",
                "## Evolution Ideas",
            ]
            for index in range(1, 6):
                rows.extend(
                    [
                        f"### idea_{index}",
                        f"- Idea: Use draft {index} to bound endpoint candidate admission by measured timing debt.",
                        "- Predicted Stage Effect: Reduce timing debt.",
                        "- Source Hooks: src/rsz/src/Timing.cc",
                        "- Source Evidence: src/rsz/src/Timing.cc::adjustTiming",
                        "- Evaluation Recipe: legacy_setup",
                        "- Expected Signals: endpoint_examined",
                        "- Falsification Condition: No official timing gain.",
                        f"- Draft Signature: draft_{index}",
                        f"- EPD Search Query: draft_{index}",
                        "- Retrieved Historical Ideas: none",
                        "- Opened EPD Records: none",
                        "- Nearest Historical Idea: none",
                        "- Semantic Overlap: no historical overlap",
                        "- Material Difference: no historical record exists",
                        "- Novelty Conclusion: new mechanism on an empty EPD",
                        "- Paper Card References: none",
                        f"- Priority: {index}",
                        "",
                    ]
                )
            rows.extend(
                [
                    "## Parent Policy",
                    "Keep the checked parent.",
                    "- Retire Pending Ideas: none",
                    "",
                    "## Student Assignments",
                    "### student_1",
                    "- Role: explorer",
                    "- Candidate:",
                    "- EPD Idea: idea_1",
                    "- Claim: Use draft 1 to bound endpoint candidate admission by measured timing debt.",
                    "- Selection Rationale: It is the most direct bounded timing experiment.",
                    "- Source Hooks: src/rsz/src/Timing.cc",
                    "- Source Evidence: src/rsz/src/Timing.cc::adjustTiming",
                    "- Evaluation Recipe: legacy_setup",
                    "- Expected Signals: endpoint_examined",
                    "- Falsification Condition: No official timing gain.",
                    "- EPD References: none",
                ]
            )
            return "\n".join(rows)

        class FakeRunner:
            def __init__(self) -> None:
                self.calls = []

            def run(self, *, operation_id, artifact_root, prompt, **_):
                self.calls.append((operation_id, prompt))
                artifact_root.mkdir(parents=True, exist_ok=True)
                last = artifact_root / "last_message.md"
                last.write_text(
                    draft_markdown() if "draft_signatures" in operation_id else final_markdown(),
                    encoding="utf-8",
                )
                events = artifact_root / "events.jsonl"
                events.write_text(
                    "\n".join(
                        json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "rg -n adjustTiming src/rsz/src/Timing.cc", "exit_code": 0}})
                        for _ in range(2)
                    ) + "\n",
                    encoding="utf-8",
                )
                return SimpleNamespace(
                    ok=True,
                    operation_id=operation_id,
                    detail="ok",
                    artifacts={"codex_last_message": str(last), "codex_events": str(events)},
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            teacher = CodexTeacher(CodexTeacherConfig())
            runner = FakeRunner()
            teacher.runner = runner
            plan = teacher.plan(
                state_root=root,
                round_root=root / "rounds" / "round_001",
                round_index=1,
                parent=self.parent,
                contract=self.contract,
                diagnosis=SimpleNamespace(to_dict=lambda: {}),
                fallback=(replace(self.hypothesis, student_id="student_1"),),
                previous_review={},
                decision_context={"stage": "timing_recovery", "evaluation_mode": "timing_only"},
            )

        self.assertEqual([call[0] for call in runner.calls], ["r001_teacher_draft_signatures", "r001_teacher_plan_novelty_review"])
        self.assertTrue(plan.plan["retrieval_audit"]["accepted"])
        self.assertEqual(len(plan.plan["draft_signatures"]), 5)
        self.assertIn("teacher_epd_retrieval_packet.json", runner.calls[1][1])

    def test_controller_materializes_teacher_authored_explorer_idea(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            hook = source / "src/rsz/src/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rsz { void adjustTiming() {} }\n", encoding="utf-8")
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=3,
                decision_context={"evaluation_mode": "timing_only"},
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1",
                        "role": "explorer",
                        "idea_reference": "idea_3",
                        "claim": "Rank one endpoint repair move by post-route timing debt.",
                        "selection_rationale": "The current timing residual is dominant.",
                        "source_hooks": ("src/rsz/src/Timing.cc",),
                        "source_evidence": ("src/rsz/src/Timing.cc::adjustTiming",),
                        "expected_signals": ("endpoint_repair_examined",),
                        "falsification_condition": "No official timing improvement with complete checks.",
                        "epd_record_ids": (),
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_3",
                        "idea": "Rank one endpoint repair move by post-route timing debt.",
                        "predicted_stage_effect": "Reduce post-route timing debt.",
                        "source_hooks": ("src/rsz/src/Timing.cc",),
                        "source_evidence": ("src/rsz/src/Timing.cc::adjustTiming",),
                        "expected_signals": ("endpoint_repair_examined",),
                        "falsification_condition": "No official timing improvement with complete checks.",
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(),
            )
        self.assertEqual(result.errors, ())
        self.assertEqual(len(result.hypotheses), 1)
        hypothesis = result.hypotheses[0]
        self.assertEqual(hypothesis.claim, "Rank one endpoint repair move by post-route timing debt.")
        self.assertEqual(hypothesis.source_hooks, ("src/rsz/src/Timing.cc",))
        self.assertEqual(hypothesis.allowed_patch_paths, ())
        self.assertEqual(hypothesis.teacher_idea_reference, "idea_3")

    def test_explorer_assignment_requires_retrieval_audit(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            hook = source / "src/rsz/src/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rsz { void adjustTiming() {} }\n", encoding="utf-8")
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=6,
                decision_context={"evaluation_mode": "timing_only"},
                portfolio={},
                suspend_explorers=False,
            )
            assignment = {
                "student_id": "student_1",
                "role": "explorer",
                "idea_reference": "idea_1",
                "claim": "Rank endpoint recovery by bounded post-route timing debt.",
                "selection_rationale": "The dominant timing debt needs a new candidate admission rule.",
                "source_hooks": ("src/rsz/src/Timing.cc",),
                "source_evidence": ("src/rsz/src/Timing.cc::adjustTiming",),
                "expected_signals": ("endpoint_recovery_examined",),
                "falsification_condition": "No official timing improvement with complete checks.",
                "epd_record_ids": (),
            }
            idea = {
                "reference": "idea_1",
                "idea": assignment["claim"],
                "predicted_stage_effect": "Reduce post-route timing debt.",
                "source_hooks": assignment["source_hooks"],
                "source_evidence": assignment["source_evidence"],
                "expected_signals": assignment["expected_signals"],
                "falsification_condition": assignment["falsification_condition"],
                "draft_signature_id": "draft_1",
                "epd_search_query": "draft_1",
                "retrieved_historical_ideas": ("IDEA_NEAR",),
                "opened_epd_records": ("IDEA_NEAR",),
                "nearest_historical_idea": "IDEA_NEAR",
                "semantic_overlap": "Both modify candidate admission.",
                "material_difference": "This uses post-route debt rather than raw slack.",
                "novelty_conclusion": "Novel because the decision state differs.",
            }
            common = {
                "assignments": (assignment,),
                "evolution_ideas": (idea,),
                "templates": templates,
                "source_root": source,
                "allowed_patch_roots": ("src/rsz",),
                "historical_ideas": (),
                "explorer_retrieval_audit": {"signatures": {"draft_1": {"accepted": False, "errors": ["missing_search_trace"]}}},
            }
            rejected = materialize_teacher_assignments(**common)
            accepted = materialize_teacher_assignments(
                **{
                    **common,
                    "explorer_retrieval_audit": {
                        "signatures": {
                            "draft_1": {
                                "accepted": True,
                                "result_ids": ["IDEA_NEAR"],
                                "opened_idea_ids": ["IDEA_NEAR"],
                            }
                        }
                    },
                }
            )

        self.assertIn("explorer_retrieval_audit_rejected:student_1", rejected.errors)
        self.assertEqual(accepted.errors, ())

    def test_controller_keeps_epd_roles_when_only_explorer_assignment_is_rejected(self) -> None:
        from goalevolve.execution.engine import _partition_controller_assignment_errors

        templates = (
            replace(self.hypothesis, student_id="student_1", student_role="explorer"),
            replace(self.hypothesis, student_id="student_2", student_role="enhancer", role_mode="epd_enhancement"),
        )
        blocking, rejected = _partition_controller_assignment_errors(
            errors=("explorer_retrieval_audit_rejected:student_1",),
            templates=templates,
        )

        self.assertEqual(blocking, ())
        self.assertEqual(rejected, ("explorer_retrieval_audit_rejected:student_1",))

    def test_repository_graph_extracts_qualified_symbols_and_includes(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            header = source / "src/rsz/Foo.hh"
            implementation = source / "src/rsz/Foo.cc"
            rmp = source / "src/rmp/Bar.cc"
            header.parent.mkdir(parents=True)
            rmp.parent.mkdir(parents=True)
            header.write_text(
                "namespace rsz { class Foo { public: void run(); }; }\n",
                encoding="utf-8",
            )
            implementation.write_text(
                '#include "Foo.hh"\n'
                "namespace rsz { void Foo::run() { helper(); } void helper() {} }\n",
                encoding="utf-8",
            )
            rmp.write_text(
                "namespace rmp { class Bar { public: void restructure(); }; }\n",
                encoding="utf-8",
            )

            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(
                source_hash="p0_content",
                allowed_patch_roots=("src/rsz", "src/rmp"),
            )

            symbol = graph.symbol_for_anchor("src/rsz/Foo.cc::rsz::Foo::run")
            self.assertIsNotNone(symbol)
            self.assertEqual(symbol.qualified_name, "rsz::Foo::run")
            self.assertEqual(
                graph.edges_of_kind("includes"),
                (("file:src/rsz/Foo.cc", "file:src/rsz/Foo.hh"),),
            )
            self.assertEqual(graph.base_source_hash, "p0_content")
            self.assertEqual(graph.allowed_patch_roots, ("src/rmp", "src/rsz"))
            self.assertTrue((graph.artifact_root / "manifest.json").is_file())
            self.assertTrue((graph.artifact_root / "graph.json").is_file())
            self.assertTrue((graph.artifact_root / "doc_cards.json").is_file())
            cards = graph.doc_cards()
            self.assertEqual(cards["file:src/rsz/Foo.cc"]["source_hash"], "p0_content")
            self.assertEqual(cards[symbol.symbol_id]["source_hash"], "p0_content")

    def test_repository_graph_excludes_module_test_sources(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            production = source / "src/rsz/src/Timing.cc"
            test = source / "src/rsz/test/cpp/TestTiming.cc"
            production.parent.mkdir(parents=True)
            test.parent.mkdir(parents=True)
            production.write_text("namespace rsz { void timing() {} }\n", encoding="utf-8")
            test.write_text("namespace rsz { void testTiming() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0")

        self.assertIn("src/rsz/src/Timing.cc", graph.files)
        self.assertNotIn("src/rsz/test/cpp/TestTiming.cc", graph.files)

    def test_source_structure_index_is_an_ast_backed_compatibility_view(self) -> None:
        from goalevolve.execution.teacher_assignment import source_structure_index
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            hook = source / "src/rsz/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text(
                "namespace rsz { void adjustTiming() {} }\n",
                encoding="utf-8",
            )
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0", allowed_patch_roots=("src/rsz",))
            index = source_structure_index(
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                repository_graph=graph,
            )

        self.assertEqual(index["src/rsz"]["sampled_files"]["src/rsz/Timing.cc"]["symbols"], ["rsz::adjustTiming"])
        self.assertEqual(set(index), {"src/rsz"})

    def test_source_structure_index_legacy_call_builds_a_fresh_ast_view(self) -> None:
        from goalevolve.execution.teacher_assignment import source_structure_index

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            hook = source / "src/rmp/Restructure.h"
            hook.parent.mkdir(parents=True)
            hook.write_text(
                "namespace rmp { struct Restructure { void run(); }; }\n",
                encoding="utf-8",
            )
            index = source_structure_index(
                source_root=source,
                allowed_patch_roots=("src/rmp",),
            )

        self.assertEqual(index["src/rmp"]["sampled_files"]["src/rmp/Restructure.h"]["symbols"], ["rmp::Restructure"])

    def test_parent_graph_reuses_p0_facts_and_reparses_only_changed_files(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p0_source = root / "p0" / "source"
            foo = p0_source / "src/rsz/Foo.cc"
            bar = p0_source / "src/rmp/Bar.cc"
            foo.parent.mkdir(parents=True)
            bar.parent.mkdir(parents=True)
            foo.write_text("namespace rsz { void foo() {} }\n", encoding="utf-8")
            bar.write_text("namespace rmp { void bar() {} }\n", encoding="utf-8")
            index = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=p0_source,
                p0_artifact_root=root / "p0" / "repository_graph",
            )
            p0_graph = index.build_p0(
                allowed_patch_roots=("src/rsz", "src/rmp"),
            )
            parent_source = root / "parent" / "source"
            shutil.copytree(p0_source, parent_source)
            (parent_source / "src/rmp/Bar.cc").write_text(
                "namespace rmp { void bar_after_patch() {} }\n",
                encoding="utf-8",
            )

            parent_graph = index.build_parent(
                source_root=parent_source,
                source_hash="parent_after_one_patch",
                allowed_patch_roots=("src/rsz", "src/rmp"),
            )

            self.assertEqual(parent_graph.base_source_hash, p0_graph.source_hash)
            self.assertEqual(parent_graph.allowed_patch_roots, ("src/rmp", "src/rsz"))
            self.assertEqual(parent_graph.artifact_root, root / "state/knowledge/repository_graph/parent_after_one_patch")
            self.assertEqual(parent_graph.reused_files, ("src/rsz/Foo.cc",))
            self.assertEqual(parent_graph.reparsed_files, ("src/rmp/Bar.cc",))
            foo_card = parent_graph.doc_card("src/rsz/Foo.cc::rsz::foo")
            self.assertEqual(foo_card["source_digest"], p0_graph.doc_card("src/rsz/Foo.cc::rsz::foo")["source_digest"])

    def test_controller_uses_graph_for_unique_qualified_source_anchor(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            hook = source / "src/rsz/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rsz { void adjustTiming() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0", allowed_patch_roots=("src/rsz",))
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=1,
                decision_context={"evaluation_mode": "timing_only"},
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1",
                        "role": "explorer",
                        "idea_reference": "idea_1",
                        "claim": "Rank the local timing work by post-route debt.",
                        "selection_rationale": "The timing residual is dominant.",
                        "source_hooks": ("src/rsz/Timing.cc",),
                        "source_evidence": ("src/rsz/Timing.cc::rsz::adjustTiming",),
                        "expected_signals": ("timing_work_examined",),
                        "falsification_condition": "No official timing improvement.",
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_1",
                        "idea": "Rank the local timing work by post-route debt.",
                        "source_hooks": ("src/rsz/Timing.cc",),
                        "source_evidence": ("src/rsz/Timing.cc::rsz::adjustTiming",),
                        "expected_signals": ("timing_work_examined",),
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(),
                repository_graph=graph,
            )
        self.assertEqual(result.errors, ())

    def test_controller_accepts_a_header_hook_with_a_unique_graph_anchor(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            hook = source / "src/rsz/RecoverPower.h"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rsz { inline void inspectHeader() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0")
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=1,
                decision_context={"evaluation_mode": "timing_only"},
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1",
                        "role": "explorer",
                        "idea_reference": "idea_1",
                        "claim": "Inspect the header-local timing boundary.",
                        "selection_rationale": "The header declares the active boundary.",
                        "source_hooks": ("src/rsz/RecoverPower.h",),
                        "source_evidence": ("src/rsz/RecoverPower.h::rsz::inspectHeader",),
                        "expected_signals": ("header_boundary_examined",),
                        "falsification_condition": "No official timing improvement.",
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_1",
                        "idea": "Inspect the header-local timing boundary.",
                        "source_hooks": ("src/rsz/RecoverPower.h",),
                        "source_evidence": ("src/rsz/RecoverPower.h::rsz::inspectHeader",),
                        "expected_signals": ("header_boundary_examined",),
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(),
                repository_graph=graph,
            )
        self.assertEqual(result.errors, ())

    def test_controller_rejects_ambiguous_short_graph_source_anchor(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            hook = source / "src/rsz/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text(
                "namespace first { void run() {} } namespace second { void run() {} }\n",
                encoding="utf-8",
            )
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0", allowed_patch_roots=("src/rsz",))
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=1,
                decision_context={"evaluation_mode": "timing_only"},
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1",
                        "role": "explorer",
                        "idea_reference": "idea_1",
                        "claim": "Rank local work by post-route debt.",
                        "selection_rationale": "The timing residual is dominant.",
                        "source_hooks": ("src/rsz/Timing.cc",),
                        "source_evidence": ("src/rsz/Timing.cc::run",),
                        "expected_signals": ("timing_work_examined",),
                        "falsification_condition": "No official timing improvement.",
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_1",
                        "idea": "Rank local work by post-route debt.",
                        "source_hooks": ("src/rsz/Timing.cc",),
                        "source_evidence": ("src/rsz/Timing.cc::run",),
                        "expected_signals": ("timing_work_examined",),
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(),
                repository_graph=graph,
            )
        self.assertEqual(
            result.errors,
            ("student_1:ambiguous_source_symbol:src/rsz/Timing.cc::run",),
        )

    def test_repository_graph_resolves_an_overload_with_its_declarator(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            implementation = source / "src/rsz/Foo.cc"
            implementation.parent.mkdir(parents=True)
            implementation.write_text(
                "namespace rsz { void Foo::run(int count) {} void Foo::run(double ratio) {} }\n",
                encoding="utf-8",
            )
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0")

        short = graph.resolve_anchor("src/rsz/Foo.cc::rsz::Foo::run")
        overload = graph.resolve_anchor("src/rsz/Foo.cc::rsz::Foo::run(int count)")
        self.assertEqual(short.status, "ambiguous")
        self.assertEqual(overload.status, "resolved")
        self.assertEqual(overload.symbols[0].declarator, "Foo::run(int count)")

    def test_controller_rejects_source_evidence_from_a_stale_graph_file(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            hook = source / "src/rsz/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rsz { void adjustTiming() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0", allowed_patch_roots=("src/rsz",))
            hook.write_text("namespace rsz { void adjustTiming() { int changed = 1; } }\n", encoding="utf-8")
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=1,
                decision_context={"evaluation_mode": "timing_only"},
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1",
                        "role": "explorer",
                        "idea_reference": "idea_1",
                        "claim": "Rank the local timing work by post-route debt.",
                        "selection_rationale": "The timing residual is dominant.",
                        "source_hooks": ("src/rsz/Timing.cc",),
                        "source_evidence": ("src/rsz/Timing.cc::rsz::adjustTiming",),
                        "expected_signals": ("timing_work_examined",),
                        "falsification_condition": "No official timing improvement.",
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_1",
                        "idea": "Rank the local timing work by post-route debt.",
                        "source_hooks": ("src/rsz/Timing.cc",),
                        "source_evidence": ("src/rsz/Timing.cc::rsz::adjustTiming",),
                        "expected_signals": ("timing_work_examined",),
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(),
                repository_graph=graph,
            )

        self.assertEqual(
            result.errors,
            ("student_1:stale_repository_graph:src/rsz/Timing.cc",),
        )

    def test_search_policy_keeps_current_parent_as_only_incumbent(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex
        from goalevolve.planning.search_policy import SearchPolicyBuilder

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            hook = source / "src/rsz/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rsz { void adjustTiming() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root,
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0", allowed_patch_roots=("src/rsz",))
            policy = SearchPolicyBuilder(root).build(
                parent=self.parent,
                diagnosis=SimpleNamespace(
                    dominant_bottleneck="tns_abs_ns",
                    to_dict=lambda: {"dominant_bottleneck": "tns_abs_ns"},
                ),
                epd_portfolio={
                    "records": [
                        {
                            "record_id": "EPD_VALIDATED",
                            "epd_status": "validated",
                            "elite_score": 0.3,
                            "source_hooks": ("src/rsz/Timing.cc",),
                        }
                    ]
                },
                repository_graph=graph,
            )

        self.assertEqual(policy["hill_climb"]["incumbent_parent_id"], self.parent.parent_id)
        self.assertEqual(policy["hill_climb"]["alternative_parent_ids"], [])
        self.assertFalse(policy["promotion_authority"])
        self.assertEqual(policy["elite_record_ids"], ["EPD_VALIDATED"])

    def test_search_policy_marks_repository_graph_disabled(self) -> None:
        from goalevolve.planning.search_policy import SearchPolicyBuilder

        with tempfile.TemporaryDirectory() as temporary:
            policy = SearchPolicyBuilder(Path(temporary)).build(
                parent=self.parent,
                diagnosis=SimpleNamespace(dominant_bottleneck="tns_abs_ns", to_dict=lambda: {}),
                epd_portfolio={"records": []},
                repository_graph=None,
            )

        self.assertEqual(policy["repository_graph"], {"enabled": False})

    def test_search_policy_requires_diversification_after_two_completed_no_promotion_rounds(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex
        from goalevolve.planning.search_policy import SearchPolicyBuilder

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            hook = source / "src/rmp/Restructure.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rmp { void restructure() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root,
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0", allowed_patch_roots=("src/rmp",))
            for round_index in (1, 2):
                atomic_json(
                    root / "rounds" / f"round_{round_index:03d}" / "round.json",
                    {"round": round_index, "promoted_student": None},
                )
            policy = SearchPolicyBuilder(root).build(
                parent=self.parent,
                diagnosis=SimpleNamespace(
                    dominant_bottleneck="leakage_power_pw",
                    to_dict=lambda: {"dominant_bottleneck": "leakage_power_pw"},
                ),
                epd_portfolio={"records": []},
                repository_graph=graph,
            )

        self.assertEqual(policy["no_promotion_streak"], 2)
        self.assertTrue(policy["diversification"]["required"])

    def test_search_policy_exposes_failed_hook_frontier_after_stagnation(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex
        from goalevolve.planning.search_policy import SearchPolicyBuilder

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            hook = source / "src/rmp/Restructure.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rmp { void restructure() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root,
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0")
            for round_index in (1, 2):
                atomic_json(
                    root / "rounds" / f"round_{round_index:03d}" / "round.json",
                    {
                        "round": round_index,
                        "promoted_student": None,
                        "teacher_plan": {
                            "hypotheses": [
                                {
                                    "hypothesis_id": f"r{round_index}_student_1",
                                    "source_hooks": ["src/rmp/Restructure.cc"],
                                }
                            ]
                        },
                        "results": [
                            {
                                "student_id": "student_1",
                                "hypothesis_id": f"r{round_index}_student_1",
                                "verdict": {
                                    "state": "refuted",
                                    "distance_gain": -0.02,
                                    "mechanism_fired": True,
                                    "reasons": ["official_contract_not_improved"],
                                },
                            }
                        ],
                    },
                )
            policy = SearchPolicyBuilder(root).build(
                parent=self.parent,
                diagnosis=SimpleNamespace(dominant_bottleneck="tns_abs_ns", to_dict=lambda: {}),
                epd_portfolio={"records": []},
                repository_graph=graph,
            )

        self.assertEqual(len(policy["stagnation"]["recent_nonpromoted_attempts"]), 2)
        self.assertEqual(
            policy["stagnation"]["repeated_hook_frontier"],
            [
                {
                    "source_hook": "src/rmp/Restructure.cc",
                    "attempt_count": 2,
                    "activation_count": 2,
                    "best_distance_gain": -0.02,
                    "last_evidence_state": "refuted",
                }
            ],
        )
        self.assertEqual(
            policy["diversification"]["avoid_exact_source_hooks"],
            ["src/rmp/Restructure.cc"],
        )

    def test_search_policy_filters_graph_cards_to_current_patch_roots(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex
        from goalevolve.planning.search_policy import SearchPolicyBuilder

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            rsz = source / "src/rsz/Timing.cc"
            rmp = source / "src/rmp/Restructure.cc"
            rsz.parent.mkdir(parents=True)
            rmp.parent.mkdir(parents=True)
            rsz.write_text("namespace rsz { void timing() {} }\n", encoding="utf-8")
            rmp.write_text("namespace rmp { void restructure() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root,
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0")
            policy = SearchPolicyBuilder(root).build(
                parent=self.parent,
                diagnosis=SimpleNamespace(dominant_bottleneck="tns_abs_ns", to_dict=lambda: {}),
                epd_portfolio={"records": []},
                repository_graph=graph,
                allowed_patch_roots=("src/rsz",),
            )

        cards = policy["repository_graph"]["focus"]["cards"]
        self.assertTrue(cards)
        self.assertTrue(all(card["path"].startswith("src/rsz/") for card in cards))

    def test_controller_selects_an_executing_recipe_for_a_phase_specific_teacher_hook(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            hook = source / "src/rsz/src/policy/SetupMt1Policy.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text(
                "void SetupMt1Policy::commitAndUpdateTiming() {}\n",
                encoding="utf-8",
            )
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=3,
                decision_context={
                    "evaluation_mode": "power_then_timing",
                    "timing_recipe_ids": {"student_1": "legacy_deep"},
                },
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1",
                        "role": "explorer",
                        "idea_reference": "idea_1",
                        "claim": "Retain each MT1 candidate only after strict global-TNS improvement.",
                        "selection_rationale": "MT1 owns this bounded journal decision.",
                        "source_hooks": ("src/rsz/src/policy/SetupMt1Policy.cc",),
                        "source_evidence": (
                            "src/rsz/src/policy/SetupMt1Policy.cc::SetupMt1Policy::commitAndUpdateTiming",
                        ),
                        "expected_signals": ("timing_mt1_incremental_examined",),
                        "falsification_condition": "No official timing gain.",
                        "epd_record_ids": (),
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_1",
                        "idea": "Retain each MT1 candidate only after strict global-TNS improvement.",
                        "predicted_stage_effect": "Reduce timing debt.",
                        "source_hooks": ("src/rsz/src/policy/SetupMt1Policy.cc",),
                        "source_evidence": (
                            "src/rsz/src/policy/SetupMt1Policy.cc::SetupMt1Policy::commitAndUpdateTiming",
                        ),
                        "expected_signals": ("timing_mt1_incremental_examined",),
                        "falsification_condition": "No official timing gain.",
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(),
            )
        self.assertEqual(result.errors, ())
        self.assertEqual(result.hypotheses[0].timing_recipe_id, "mt1_deep")

    def test_controller_honors_teacher_declared_rmp_recipe_for_a_generic_hook(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            hook = source / "src/rmp/src/Restructure.cpp"
            hook.parent.mkdir(parents=True)
            hook.write_text(
                "void Restructure::runABC() {}\n",
                encoding="utf-8",
            )
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=4,
                decision_context={
                    "evaluation_mode": "power_then_timing",
                    "timing_recipe_ids": {"student_1": "wns_path_deep"},
                },
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1",
                        "role": "explorer",
                        "idea_reference": "idea_2",
                        "evaluation_recipe": "rmp_path_cone_halo_timing",
                        "claim": "Recheck selected-state STA before retaining an RMP trial.",
                        "selection_rationale": "The RMP selected-state boundary is untested.",
                        "source_hooks": ("src/rmp/src/Restructure.cpp",),
                        "source_evidence": ("src/rmp/src/Restructure.cpp::Restructure::runABC",),
                        "expected_signals": ("rmp_selected_state_recheck",),
                        "falsification_condition": "No official timing gain.",
                        "epd_record_ids": (),
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_2",
                        "idea": "Recheck selected-state STA before retaining an RMP trial.",
                        "evaluation_recipe": "rmp_path_cone_halo_timing",
                        "predicted_stage_effect": "Avoid non-reproducible RMP trial retention.",
                        "source_hooks": ("src/rmp/src/Restructure.cpp",),
                        "source_evidence": ("src/rmp/src/Restructure.cpp::Restructure::runABC",),
                        "expected_signals": ("rmp_selected_state_recheck",),
                        "falsification_condition": "No official timing gain.",
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rmp",),
                historical_ideas=(),
            )
        self.assertEqual(result.errors, ())
        self.assertEqual(result.hypotheses[0].timing_recipe_id, "rmp_path_cone_halo_timing")

    def test_controller_accepts_structurally_grounded_explorer_paraphrase(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            hook = source / "src/rsz/src/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("namespace rsz { void adjustTiming() {} }\n", encoding="utf-8")
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=3,
                decision_context={"evaluation_mode": "timing_only"},
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1",
                        "role": "explorer",
                        "idea_reference": "idea_3",
                        "claim": "Prioritize the most negative timing slack before existing score ties.",
                        "selection_rationale": "The current timing residual is dominant.",
                        "source_hooks": ("src/rsz/src/Timing.cc",),
                        "source_evidence": ("src/rsz/src/Timing.cc::adjustTiming",),
                        "expected_signals": ("timing_move_examined",),
                        "falsification_condition": "No official timing improvement with complete checks.",
                        "epd_record_ids": (),
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_3",
                        "idea": (
                            "Within the bounded timing repair loop, prioritize existing legal candidates "
                            "with the most negative collected timing slack, then use the existing score "
                            "only for deterministic tie breaking."
                        ),
                        "predicted_stage_effect": "Reduce post-route timing debt.",
                        "source_hooks": ("src/rsz/src/Timing.cc",),
                        "source_evidence": ("src/rsz/src/Timing.cc::adjustTiming",),
                        "expected_signals": ("timing_move_examined",),
                        "falsification_condition": "No official timing improvement with complete checks.",
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(),
            )
        self.assertEqual(result.errors, ())
        self.assertEqual(len(result.hypotheses), 1)

    def test_controller_rejects_duplicate_explorer_but_not_epd_enhancer(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            hook = source / "src/rsz/src/Timing.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("void adjustTiming() {}\n", encoding="utf-8")
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=4,
                decision_context={},
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=(
                    {
                        "student_id": "student_1", "role": "explorer", "idea_reference": "idea_1",
                        "claim": "Rank one endpoint repair move by post-route timing debt.",
                        "selection_rationale": "timing", "source_hooks": ("src/rsz/src/Timing.cc",),
                        "source_evidence": ("src/rsz/src/Timing.cc::adjustTiming",),
                        "expected_signals": ("endpoint_repair_examined",),
                        "falsification_condition": "No gain.", "epd_record_ids": (),
                    },
                ),
                evolution_ideas=(
                    {
                        "reference": "idea_1", "idea": "Rank one endpoint repair move by post-route timing debt.",
                        "predicted_stage_effect": "timing", "source_hooks": ("src/rsz/src/Timing.cc",),
                        "source_evidence": ("src/rsz/src/Timing.cc::adjustTiming",),
                        "expected_signals": ("endpoint_repair_examined",), "falsification_condition": "No gain.",
                    },
                ),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(
                    {"idea": "Rank one endpoint repair move by post-route timing debt.", "status": "validated", "source_hooks": ["src/rsz/src/Timing.cc"]},
                ),
            )
        self.assertEqual(result.hypotheses, ())
        self.assertIn("duplicate_explorer_idea:student_1", result.errors)

    def test_role_schedule_suspends_explorers_for_one_bottleneck_transition(self) -> None:
        from goalevolve.execution.teacher_assignment import build_role_templates

        templates = build_role_templates(
            student_ids=("student_1", "student_2", "student_3", "student_4"),
            round_index=7,
            decision_context={"evaluation_mode": "power_then_timing"},
            portfolio={
                "integration_candidates": [["EPD_timing", "EPD_power"]],
                "enhancement_candidates": ["EPD_power"],
            },
            suspend_explorers=True,
        )
        self.assertEqual([item.student_role for item in templates], ["integrator", "enhancer"])
        self.assertEqual([item.student_id for item in templates], ["student_1", "student_2"])
        self.assertEqual(templates[0].candidate_options[0]["epd_record_ids"], ("EPD_timing", "EPD_power"))

    def test_role_schedule_expands_to_four_explorers_without_epd_roles(self) -> None:
        from goalevolve.execution.teacher_assignment import build_role_templates

        templates = build_role_templates(
            student_ids=("student_1", "student_2", "student_3", "student_4"),
            round_index=7,
            decision_context={"evaluation_mode": "timing_only"},
            portfolio={},
            suspend_explorers=True,
        )

        self.assertEqual(
            [item.student_role for item in templates],
            ["explorer", "explorer", "explorer", "explorer"],
        )
        self.assertEqual(
            [item.student_id for item in templates],
            ["student_1", "student_2", "student_3", "student_4"],
        )

    def test_role_schedule_keeps_two_explorers_when_an_epd_role_is_available(self) -> None:
        from goalevolve.execution.teacher_assignment import build_role_templates

        templates = build_role_templates(
            student_ids=("student_1", "student_2", "student_3", "student_4"),
            round_index=7,
            decision_context={"evaluation_mode": "timing_only"},
            portfolio={"integration_candidates": [["EPD_timing", "EPD_power"]]},
            suspend_explorers=False,
        )

        self.assertEqual(
            [item.student_role for item in templates],
            ["explorer", "explorer", "integrator"],
        )

    def test_paper_card_reference_penalizes_use_above_five(self) -> None:
        from goalevolve.planning.retrieval import DiverseRetriever

        cards = (
            MechanismCard("frequent", "frequent", ("timing",), ("src/rsz/src/A.cc",), ("a",), "frequent"),
            MechanismCard("fresh", "fresh", ("timing",), ("src/rsz/src/B.cc",), ("b",), "fresh"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            retriever = DiverseRetriever(cards)
            for index in range(6):
                retriever.record_paper_card_references(
                    state_root=root, round_index=index + 1, card_ids=("frequent",)
                )
            references = retriever.paper_card_references(
                parent=self.parent, symptoms=("timing",), state_root=root, count=2
            )
        self.assertEqual(references[0]["card_id"], "fresh")
        self.assertTrue(next(row for row in references if row["card_id"] == "frequent")["overuse_penalty"])

    def test_paper_card_reference_exposes_topics_not_patch_recipe(self) -> None:
        from goalevolve.planning.retrieval import DiverseRetriever

        cards = (
            MechanismCard(
                "timing_reference",
                "timing_family",
                ("tns", "timing"),
                ("src/rsz/src/Timing.cc",),
                ("timing_examined",),
                "Copy this exact source-level candidate ordering patch.",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            references = DiverseRetriever(cards).paper_card_references(
                parent=self.parent,
                symptoms=("timing",),
                state_root=Path(temporary),
                count=1,
            )
        self.assertEqual(references[0]["topic_tags"], ["tns", "timing"])
        self.assertNotIn("claim", references[0])
        self.assertNotIn("mechanism_family", references[0])
        self.assertNotIn("source_hooks", references[0])

    def test_teacher_markdown_protocol_requires_source_investigation_for_explorer_anchors(self) -> None:
        from goalevolve.agents.markdown_protocol import teacher_plan_validation_errors

        markdown = """## Diagnosis Summary
Timing remains active.

## Evolution Ideas
### idea_1
- Idea: Add a bounded timing guard.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Timing.cc
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Expected Signals: timing_guard_examined
- Falsification Condition: No official QoR gain.
- Paper Card References: none
- Priority: 0

## Parent Policy
Keep the checked parent.

## Student Assignments
### student_1
- Role: explorer
- Candidate:
- EPD Idea: idea_1
- Claim: Add a bounded timing guard.
- Selection Rationale: It targets timing.
- Source Hooks: src/rsz/src/Timing.cc
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Expected Signals: timing_guard_examined
- Falsification Condition: No official QoR gain.
- EPD References: none
"""
        errors = teacher_plan_validation_errors(
            markdown,
            required_roles=("explorer",),
            require_explorer_ideas=False,
            required_student_roles={"student_1": "explorer"},
        )
        self.assertIn("missing_section:source_investigation", errors)

    def test_teacher_markdown_protocol_keeps_blank_explorer_candidate_blank(self) -> None:
        from goalevolve.agents.markdown_protocol import parse_teacher_plan

        parsed = parse_teacher_plan(
            """## Student Assignments
### student_1
- Role: explorer
- Candidate:
- EPD Idea: idea_1
"""
        )
        self.assertEqual(parsed["assignments"][0]["candidate_id"], "")
        self.assertEqual(parsed["assignments"][0]["idea_reference"], "idea_1")

    def test_teacher_markdown_protocol_splits_semicolon_separated_source_hooks(self) -> None:
        from goalevolve.agents.markdown_protocol import parse_teacher_plan

        parsed = parse_teacher_plan(
            """## Evolution Ideas
### idea_1
- Idea: Guard two source files.
- Source Hooks: src/rsz/src/RepairPowerPolicy.cc; src/rsz/src/Resizer.cc

## Student Assignments
### student_1
- Role: explorer
- Source Hooks: src/rsz/src/RepairPowerPolicy.cc; src/rsz/src/Resizer.cc
"""
        )
        expected = (
            "src/rsz/src/RepairPowerPolicy.cc",
            "src/rsz/src/Resizer.cc",
        )
        self.assertEqual(parsed["evolution_idea_records"][0]["source_hooks"], expected)
        self.assertEqual(parsed["assignments"][0]["source_hooks"], expected)

    def test_incomplete_teacher_plan_normalizes_legacy_combined_source_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            from goalevolve.execution.engine import GoalEvolveEngine

            round_root = Path(temporary) / "round_001"
            round_root.mkdir()
            hypothesis = self.hypothesis.to_dict()
            hypothesis["source_hooks"] = [
                "src/rsz/src/RepairPowerPolicy.cc; src/rsz/src/Resizer.cc"
            ]
            atomic_json(round_root / "teacher_plan.json", {"hypotheses": [hypothesis]})
            recovered = GoalEvolveEngine._incomplete_teacher_plan(round_root)

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(
            recovered[0][0].source_hooks,
            ("src/rsz/src/RepairPowerPolicy.cc", "src/rsz/src/Resizer.cc"),
        )

    def test_controller_repair_markdown_accepts_only_semicolon_hook_rejections(self) -> None:
        payload = {
            "controller_assignment_errors": [
                "student_2:missing_source_hook:src/rsz/src/One.cc; src/rsz/src/Two.cc"
            ],
            "controller_assignment_repair": {
                "teacher_ok": True,
                "format_errors": [],
                "teacher_markdown": "## Student Assignments\n",
            },
        }
        self.assertEqual(
            GoalEvolveEngine._controller_repair_markdown(payload),
            "## Student Assignments",
        )
        payload["controller_assignment_errors"] = ["student_2:missing_source_hook:src/rsz/src/Missing.cc"]
        self.assertEqual(GoalEvolveEngine._controller_repair_markdown(payload), "")

    def test_incomplete_controller_recovery_uses_last_structurally_valid_repair(self) -> None:
        payload = {
            "teacher_markdown": "## original plan",
            "controller_assignment_repairs": [
                {
                    "teacher_ok": True,
                    "format_errors": [],
                    "teacher_markdown": "## valid repair",
                },
                {
                    "teacher_ok": True,
                    "format_errors": ["teacher_source_inspection_not_observed"],
                    "teacher_markdown": "## audit-invalid repair",
                },
            ],
        }
        self.assertEqual(
            GoalEvolveEngine._recoverable_teacher_markdown(payload),
            "## valid repair",
        )

    def test_source_hook_materialization_requires_an_executed_policy_recipe(self) -> None:
        from goalevolve.execution.teacher_assignment import (
            build_role_templates,
            materialize_teacher_assignments,
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            policy = source / "src/rsz/src/policy/SetupCritVtSwapPolicy.cc"
            policy.parent.mkdir(parents=True)
            policy.write_text("namespace rsz { void selectCritVtCell() {} }\n", encoding="utf-8")
            templates = build_role_templates(
                student_ids=("student_1",),
                round_index=1,
                decision_context={"evaluation_mode": "power_only"},
                portfolio={},
                suspend_explorers=False,
            )
            result = materialize_teacher_assignments(
                assignments=({
                    "student_id": "student_1", "role": "explorer", "idea_reference": "idea_1",
                    "claim": "Touch a critical-VT policy.", "selection_rationale": "Probe the policy.",
                    "source_hooks": ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc",),
                    "source_evidence": ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc::selectCritVtCell",),
                    "expected_signals": ("crit_vt_probe",), "evaluation_recipe": "legacy_setup",
                    "falsification_condition": "No official gain.",
                },),
                evolution_ideas=({
                    "reference": "idea_1", "idea": "Touch a critical-VT policy.",
                    "source_hooks": ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc",),
                    "source_evidence": ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc::selectCritVtCell",),
                    "expected_signals": ("crit_vt_probe",), "evaluation_recipe": "legacy_setup",
                },),
                templates=templates,
                source_root=source,
                allowed_patch_roots=("src/rsz",),
                historical_ideas=(),
            )
        self.assertIn("incompatible_evaluation_recipe:student_1:legacy_setup", result.errors)

    def test_repair_power_policy_requires_a_power_executing_mode(self) -> None:
        from goalevolve.planning.timing_recovery import (
            recipe_is_compatible_with_source_hooks,
        )

        hooks = ("src/rsz/src/policy/RepairPowerPolicy.cc",)
        self.assertFalse(
            recipe_is_compatible_with_source_hooks(
                "legacy_setup", hooks, evaluation_mode="timing_only"
            )
        )
        self.assertTrue(
            recipe_is_compatible_with_source_hooks(
                "legacy_setup", hooks, evaluation_mode="power_only"
            )
        )
        self.assertTrue(
            recipe_is_compatible_with_source_hooks(
                "legacy_deep", hooks, evaluation_mode="power_then_timing"
            )
        )

    def test_unexecuted_power_recovery_plus_policy_is_rejected(self) -> None:
        from goalevolve.planning.timing_recovery import (
            recipe_is_compatible_with_source_hooks,
        )

        self.assertFalse(
            recipe_is_compatible_with_source_hooks(
                "legacy_setup",
                ("src/rsz/src/policy/PowerRecoveryPlusPolicy.cc",),
                evaluation_mode="power_only",
            )
        )

    def test_teacher_source_inspection_audit_requires_successful_source_reads(self) -> None:
        from goalevolve.agents.teacher import source_inspection_audit

        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events.jsonl"
            events.write_text(
                "{\"type\": \"item.completed\", \"item\": {\"type\": \"command_execution\", \"command\": \"rg -n adjustTiming src/rsz/src/Timing.cc\", \"exit_code\": 0, \"status\": \"completed\"}}\n",
                encoding="utf-8",
            )
            audit = source_inspection_audit((events,))
        self.assertFalse(audit["satisfied"])
        self.assertEqual(audit["successful_source_commands"], 1)

    def test_teacher_prompt_requires_source_investigation_before_evolution_ideas(self) -> None:
        prompt = CodexTeacher._plan_prompt(
            parent=self.parent,
            diagnosis=SimpleNamespace(to_dict=lambda: {}),
            epd={},
            observations={},
            previous_review={},
            fallback=(replace(self.hypothesis, student_id="student_1"),),
        )
        self.assertLess(prompt.index("## Source Investigation"), prompt.index("## Evolution Ideas"))

    def test_teacher_repair_prompt_repeats_controller_execution_contract(self) -> None:
        prompt = CodexTeacher._plan_repair_prompt(
            prior_markdown="## Prior",
            errors=("incompatible_evaluation_recipe:student_1:legacy_setup",),
            required_student_roles={"student_1": "explorer"},
            require_explorer_ideas=True,
            allowed_recipe_ids=("legacy_setup", "rmp_area_power"),
            execution_contracts={
                "power_only": {
                    "source_hook_rule": "Do not name Setup* or PowerRecoveryPlusPolicy as Source Hooks.",
                }
            },
        )
        self.assertIn("## Controller Execution Contracts", prompt)
        self.assertIn("Do not name Setup* or PowerRecoveryPlusPolicy", prompt)

    def test_teacher_prompt_includes_p0_rooted_doc_card_packet(self) -> None:
        prompt = CodexTeacher._plan_prompt(
            parent=self.parent,
            diagnosis=SimpleNamespace(to_dict=lambda: {}),
            epd={},
            observations={},
            previous_review={},
            fallback=(replace(self.hypothesis, student_id="student_1"),),
            repository_graph={
                "source_hash": "parent_hash",
                "base_source_hash": "frozen_p0",
                "artifact_root": "/state/knowledge/repository_graph/parent_hash",
                "cards": [{"path": "src/rsz/Timing.cc", "qualified_name": "rsz::adjustTiming"}],
            },
        )
        self.assertIn("## P0-rooted Source Graph and Doc Cards", prompt)
        self.assertIn("frozen_p0", prompt)
        self.assertIn("rsz::adjustTiming", prompt)

    def test_teacher_prompt_omits_repository_graph_for_graph_free_ablation(self) -> None:
        prompt = CodexTeacher._plan_prompt(
            parent=self.parent,
            diagnosis=SimpleNamespace(to_dict=lambda: {}),
            epd={},
            observations={},
            previous_review={},
            fallback=(replace(self.hypothesis, student_id="student_1"),),
            source_index={},
            repository_graph=None,
        )

        self.assertIn("## Source Localization", prompt)
        self.assertIn("Repository graph is disabled for this ablation", prompt)
        self.assertNotIn("## P0-rooted Source Graph and Doc Cards", prompt)
        self.assertNotIn("## Compact Source Structure Index", prompt)

    def test_teacher_packet_is_compact_and_evidence_routed(self) -> None:
        diagnosis = SimpleNamespace(
            to_dict=lambda: {
                "dominant_bottleneck": "tns_abs_ns",
                "dominant_residual": 0.25,
                "checkpoint_effects": {"post_repair_power": {"tns_abs_ns": -2.0}},
                "unresolved_debt": {"tns_abs_ns": 2.0},
                "parent_goal_distance": self.parent.goal_distance,
            }
        )
        prompt = CodexTeacher._plan_prompt(
            parent=self.parent,
            diagnosis=diagnosis,
            contract=self.contract,
            epd={
                "full_epd_artifact": "/state/knowledge/epd.json",
                "status_counts": {"promising": 1},
                "decision_records": [{"record_id": "EPD_KEEP", "idea_id": "IDEA_KEEP", "mechanism_family": "keep", "epd_status": "promising", "metrics": {"tns_abs_ns": 99.0}}],
                "pending_ideas": [{"idea_id": "IDEA_PENDING", "idea": "A bounded mechanism paragraph.", "status": "pending"}],
            },
            observations={
                "family_hook_summary": [
                    {"mechanism_family": f"family_{index}", "source_hooks": [f"src/rsz/{index}.cc"], "activation_count": index % 2, "best_distance_gain": float(index), "last_state": "invalid", "last_failure_signature": "bounded failure"}
                    for index in range(12)
                ]
            },
            schedule_memory={"full_schedule_memory_artifact": "/state/knowledge/timing_schedule_memory.json", "active_rules": [{"rule": "preserve timing handoff"}]},
            previous_review={"round_assessment": "Student found a downstream reversal.", "mechanism_actions": [{"family": "keep", "action": "refine"}]},
            fallback=(replace(self.hypothesis, student_id="student_1"),),
            decision_context={"stage": "adaptive_tradeoff", "dominant_metric": "tns_abs_ns", "evaluation_mode": "power_then_timing", "falsification_rule": "hold the frozen contract"},
            source_index={"src/rsz/huge.cc": ["giant_index_symbol"]},
            repository_graph={
                "entry_chain": ["Resizer::repairPower", "RepairPowerPolicy::iterate"],
                "focused_files": ["src/rsz/src/Resizer.cc"],
                "focused_graph_path": "/state/knowledge/repository_graph/focus.json",
                "full_index_path": "/state/knowledge/repository_graph/compact_index.json",
                "full_graph_path": "/state/knowledge/repository_graph/graph.json",
            },
            search_policy={
                "no_promotion_streak": 2,
                "diversification": {"avoid_exact_source_hooks": ["src/rsz/reused.cc"]},
                "hill_climb": {"incumbent_parent_id": "baseline"},
                "promotion_authority": False,
                "diagnosis": {"duplicated": "must not be copied"},
            },
            source_root=Path("/state/parents/hash/source"),
        )

        self.assertTrue(prompt.startswith("## Packet Usage Guide"))
        for heading in (
            "## Goal Contract",
            "## Active Decision Stage",
            "## Diagnosis",
            "## EPD (idea lifecycle and compact attempts)",
            "## Observation Memory",
            "## Timing Schedule / Cell-Reversal Memory",
            "## Student Reflection Digest",
            "## Controller Role Envelopes",
            "## Evidence-only Search Policy",
        ):
            self.assertEqual(prompt.count(heading + "\n"), 1, heading)
        self.assertNotIn("## Parent\n", prompt)
        self.assertIn("- tns_abs_ns: baseline 100 → target ≤ 50", prompt)
        self.assertIn("Parent QoR: tns_abs_ns=100", prompt)
        self.assertLess(prompt.index("Current stage: adaptive_tradeoff"), prompt.index('"stage": "adaptive_tradeoff"'))
        self.assertIn('"checkpoint_effects"', prompt)
        self.assertNotIn('"parent_goal_distance"', prompt)
        self.assertIn("/state/knowledge/epd/indexes/idea_catalog.jsonl", prompt)
        self.assertNotIn("giant_index_symbol", prompt)
        self.assertIn("/state/knowledge/repository_graph/focus.json", prompt)
        self.assertEqual(sum(f"family_{index}" in prompt for index in range(12)), 10)
        self.assertIn("no_promotion_streak: 2", prompt)
        self.assertIn("promotion_authority: Controller only", prompt)

    def test_repository_graph_focus_filters_cards_to_allowed_patch_roots(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            rsz = source / "src/rsz/Timing.cc"
            rmp = source / "src/rmp/Restructure.cc"
            rsz.parent.mkdir(parents=True)
            rmp.parent.mkdir(parents=True)
            rsz.write_text("namespace rsz { void timing() {} }\n", encoding="utf-8")
            rmp.write_text("namespace rmp { void restructure() {} }\n", encoding="utf-8")
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0")
            packet = graph.focus(allowed_patch_roots=("src/rsz",))

        self.assertTrue(packet["cards"])
        self.assertTrue(all(card["path"].startswith("src/rsz/") for card in packet["cards"]))

    def test_repository_graph_focus_emits_a_closed_induced_subgraph(self) -> None:
        from goalevolve.planning.repository_graph import RepositoryGraphIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            header = source / "src/rsz/Foo.hh"
            implementation = source / "src/rsz/Foo.cc"
            header.parent.mkdir(parents=True)
            header.write_text("namespace rsz { class Foo {}; }\n", encoding="utf-8")
            implementation.write_text(
                '#include "Foo.hh"\n'
                "namespace rsz { void run() { helper(); } void helper() {} }\n",
                encoding="utf-8",
            )
            graph = RepositoryGraphIndex(
                state_root=root / "state",
                p0_source_root=source,
                p0_artifact_root=root / "p0_graph",
            ).build_p0(source_hash="p0")
            packet = graph.focus(
                anchor_hints=("src/rsz/Foo.cc::rsz::run",),
                allowed_patch_roots=("src/rsz",),
                max_cards=3,
            )

        card_ids = {card["card_id"] for card in packet["cards"]}
        self.assertTrue(packet["edges"])
        self.assertEqual(packet["simplification"]["selected_symbol_count"], 3)
        self.assertGreaterEqual(packet["simplification"]["one_hop_call_neighbor_count"], 1)
        self.assertTrue(any(edge["kind"] == "calls" for edge in packet["edges"]))
        self.assertTrue(any(edge["kind"] == "includes" for edge in packet["edges"]))
        self.assertIn("rsz::run", {card.get("qualified_name") for card in packet["cards"]})
        self.assertIn("rsz::helper", {card.get("qualified_name") for card in packet["cards"]})
        self.assertTrue(
            all(edge["source"] in card_ids and edge["target"] in card_ids for edge in packet["edges"])
        )
        for card in packet["cards"]:
            for field in ("contains", "includes", "included_by", "calls", "called_by"):
                self.assertTrue(set(card.get(field, ())).issubset(card_ids))

    def test_codex_teacher_retries_invalid_markdown_in_the_same_thread(self) -> None:
        from goalevolve.agents.codex_runtime import CodexTurn
        from goalevolve.agents.teacher import CodexTeacher, CodexTeacherConfig

        class ScriptedRunner:
            def __init__(self, messages):
                self.messages = iter(messages)
                self.calls = []

            def run(self, *, identity, operation_id, artifact_root, **_):
                self.calls.append((identity, operation_id))
                artifact_root.mkdir(parents=True, exist_ok=True)
                message = artifact_root / "last_message.md"
                message.write_text(next(self.messages), encoding="utf-8")
                events = artifact_root / "events.jsonl"
                events.write_text(
                    (
                        "{\"type\": \"item.completed\", \"item\": {\"type\": \"command_execution\", \"command\": \"rg -n adjustTiming src/rsz/src/Timing.cc\", \"exit_code\": 0}}\n"
                        "{\"type\": \"item.completed\", \"item\": {\"type\": \"command_execution\", \"command\": \"sed -n '1,80p' src/rsz/src/Timing.cc\", \"exit_code\": 0}}\n"
                        if len(self.calls) == 1
                        else ""
                    ),
                    encoding="utf-8",
                )
                return CodexTurn(
                    True,
                    operation_id,
                    "thread-1",
                    "ok",
                    {"codex_last_message": str(message), "codex_events": str(events)},
                )

        valid = """## Diagnosis Summary
Timing is active.

## Source Investigation
### investigation_1
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Observed Control Point: Existing timing candidate ordering is inside the policy loop.
### investigation_2
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Observed Control Point: The current journal boundary accepts bounded telemetry.

## Evolution Ideas
### idea_1
- Idea: Rank a bounded timing move.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Timing.cc
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Evaluation Recipe: legacy_deep
- Expected Signals: timing_move_examined
- Falsification Condition: No official gain.
- Paper Card References: none
- Priority: 0
### idea_2
- Idea: Bound endpoint repair admission.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Timing.cc
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Expected Signals: endpoint_admission_examined
- Falsification Condition: No official gain.
- Paper Card References: none
- Priority: 1
### idea_3
- Idea: Preserve post-route timing reserve.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Timing.cc
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Expected Signals: timing_reserve_examined
- Falsification Condition: No official gain.
- Paper Card References: none
- Priority: 2
### idea_4
- Idea: Reject a timing regression before commit.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Timing.cc
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Expected Signals: timing_rejection_examined
- Falsification Condition: No official gain.
- Paper Card References: none
- Priority: 3
### idea_5
- Idea: Measure a local timing recovery choice.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Timing.cc
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Expected Signals: timing_choice_examined
- Falsification Condition: No official gain.
- Paper Card References: none
- Priority: 4

## Parent Policy
Keep the checked parent.

## Student Assignments
### student_1
- Role: explorer
- Candidate:
- EPD Idea: idea_1
- Claim: Rank a bounded timing move.
- Selection Rationale: Timing is active.
- Source Hooks: src/rsz/src/Timing.cc
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Evaluation Recipe: legacy_deep
- Expected Signals: timing_move_examined
- Falsification Condition: No official gain.
- EPD References: none
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            teacher = CodexTeacher(CodexTeacherConfig(max_plan_format_repairs=1))
            runner = ScriptedRunner(("## Diagnosis Summary\nmissing required blocks", valid))
            teacher.runner = runner
            plan = teacher.plan(
                state_root=root,
                round_root=root / "round",
                round_index=4,
                parent=self.parent,
                diagnosis=SimpleNamespace(to_dict=lambda: {}),
                fallback=(replace(self.hypothesis, student_id="student_1"),),
                previous_review={},
            )
        self.assertTrue(plan.plan["format_valid"])
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(runner.calls[0][0], runner.calls[1][0])
        self.assertIn("format_repair", runner.calls[1][1])

    def test_codex_engine_uses_teacher_authored_mechanisms_not_planner_cards(self) -> None:
        from goalevolve.agents.markdown_protocol import parse_teacher_plan
        from goalevolve.agents.teacher import TeacherPlan

        class Teacher:
            name = "codex_teacher"

            def plan(self, *, diagnosis, fallback, **_):
                markdown = """## Diagnosis Summary
Timing is the only active residual.

## Evolution Ideas
### idea_1
- Idea: Rank endpoint recovery by current timing debt.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Teacher.cc
- Source Evidence: src/rsz/src/Teacher.cc::rankEndpointRecovery
- Expected Signals: endpoint_recovery_examined
- Falsification Condition: No official timing gain.
- Paper Card References: none
- Priority: 0
### idea_2
- Idea: Reject endpoint recovery after route regression.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Teacher.cc
- Source Evidence: src/rsz/src/Teacher.cc::rankEndpointRecovery
- Expected Signals: endpoint_route_rejection_examined
- Falsification Condition: No official timing gain.
- Paper Card References: none
- Priority: 1
### idea_3
- Idea: Track a bounded endpoint timing reserve.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Teacher.cc
- Source Evidence: src/rsz/src/Teacher.cc::rankEndpointRecovery
- Expected Signals: endpoint_reserve_examined
- Falsification Condition: No official timing gain.
- Paper Card References: none
- Priority: 2
### idea_4
- Idea: Gate endpoint repair by local slack direction.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Teacher.cc
- Source Evidence: src/rsz/src/Teacher.cc::rankEndpointRecovery
- Expected Signals: endpoint_slack_gate_examined
- Falsification Condition: No official timing gain.
- Paper Card References: none
- Priority: 3
### idea_5
- Idea: Limit endpoint recovery to the current bottleneck.
- Predicted Stage Effect: Reduce timing debt.
- Source Hooks: src/rsz/src/Teacher.cc
- Source Evidence: src/rsz/src/Teacher.cc::rankEndpointRecovery
- Expected Signals: endpoint_bottleneck_examined
- Falsification Condition: No official timing gain.
- Paper Card References: none
- Priority: 4

## Parent Policy
Keep the checked parent.

## Student Assignments
### student_1
- Role: explorer
- Candidate:
- EPD Idea: idea_1
- Claim: Rank endpoint recovery by current timing debt.
- Selection Rationale: It directly tests the active timing residual.
- Source Hooks: src/rsz/src/Teacher.cc
- Source Evidence: src/rsz/src/Teacher.cc::rankEndpointRecovery
- Expected Signals: endpoint_recovery_examined
- Falsification Condition: No official timing gain.
- EPD References: none
### student_2
- Role: explorer
- Candidate:
- EPD Idea: idea_2
- Claim: Reject endpoint recovery after route regression.
- Selection Rationale: It independently protects the post-route result.
- Source Hooks: src/rsz/src/Teacher.cc
- Source Evidence: src/rsz/src/Teacher.cc::rankEndpointRecovery
- Expected Signals: endpoint_route_rejection_examined
- Falsification Condition: No official timing gain.
- EPD References: none
"""
                parsed = parse_teacher_plan(markdown)
                return TeacherPlan((), diagnosis, {
                    "teacher_markdown": markdown,
                    "parsed_markdown": parsed,
                    "format_valid": True,
                    "hypotheses": [],
                }, {})

            def repair_plan_after_controller_validation(self, **_):
                raise AssertionError("Teacher assignment should already be valid")

            def review(self, **_):
                return {}

        class StaticEditor:
            name = "static"
            def apply(self, **_):
                return StudentEditReport(True, "edited", "static", None, {})
            def repair(self, **_):
                return StudentEditReport(False, "not_needed", "repair", None, {})

        class StaticEvaluator:
            name = "static"
            config = SimpleNamespace(allowed_patch_roots=("src/rsz",))
            def evaluate(self, *, parent, hypothesis, student_id, **_):
                metrics = dict(parent.metrics)
                metrics["tns_abs_ns"] = float(metrics["tns_abs_ns"]) - 1.0
                return CandidateResult(
                    student_id, hypothesis, metrics,
                    {signal: 1.0 for signal in hypothesis.expected_signals},
                    [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                    f"+++ b/{hypothesis.source_hooks[0]}\n+// teacher idea\n",
                    f"commit-{student_id}",
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "seed"
            hook = source / "src/rsz/src/Teacher.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("void rankEndpointRecovery() {}\n", encoding="utf-8")
            planner = DiversePlanner(DiverseRetriever((
                MechanismCard("planner_card", "planner", ("timing",), ("src/rsz/src/Planner.cc",), ("planner",), "planner card"),
            )))
            engine = GoalEvolveEngine(
                self.contract, root / "campaign", planner, StaticEvaluator(),
                IsolatedWorkspace(source), StrictEvidencePromotion(),
                student_ids=("student_1", "student_2"), student_editor=StaticEditor(), teacher=Teacher(),
            )
            engine.initialize(baseline_metrics=dict(self.parent.metrics))
            engine.run(rounds=1)
            plan = load_json(root / "campaign" / "rounds" / "round_001" / "teacher_plan.json")
            self.assertTrue((root / "campaign" / "rounds" / "round_001" / "search_policy.json").is_file())
        hypotheses = list(plan["hypotheses"])
        self.assertEqual([row["source_hooks"] for row in hypotheses], [["src/rsz/src/Teacher.cc"], ["src/rsz/src/Teacher.cc"]])
        self.assertTrue(all(row["retrieval_ids"][0].startswith("teacher_idea:") for row in hypotheses))
        self.assertNotIn("planner_card", str(hypotheses))
        self.assertEqual(plan["repository_graph"]["source_hash"], "baseline")
        self.assertTrue(plan["repository_graph"]["base_source_hash"])
        self.assertIn("knowledge/repository_graph/baseline", plan["repository_graph"]["artifact_root"])
        self.assertEqual(plan["search_policy"]["hill_climb"]["incumbent_parent_id"], "baseline")
        self.assertFalse(plan["search_policy"]["promotion_authority"])

    def test_epd_role_portfolio_uses_distinct_validated_qor_islands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            timing = Hypothesis(
                "timing", "timing", "timing claim", ("src/rsz/src/Timing.cc",),
                ("timing_examined",), ("timing_card",), "timing",
            )
            power = Hypothesis(
                "power", "power", "power claim", ("src/rsz/src/Power.cc",),
                ("power_retained",), ("power_card",), "power",
            )
            timing_record = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_1", timing,
                    {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0},
                    {"timing_examined": 1.0}, checks, "+++ b/src/rsz/src/Timing.cc\n+change\n", "timing-source",
                ),
                verdict=EvidenceVerdict("validated", 0.1, 0.4, True, True, ()),
            )
            power_record = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_2", power,
                    {"tns_abs_ns": 100.0, "leakage_power_pw": 170.0},
                    {"power_retained": 1.0}, checks, "+++ b/src/rsz/src/Power.cc\n+change\n", "power-source",
                ),
                verdict=EvidenceVerdict("validated", 0.15, 0.35, True, True, ()),
            )
            portfolio = epd.role_portfolio(contract=self.contract, parent=self.parent)
        self.assertEqual(portfolio["enhancement_record_ids"], [])
        self.assertEqual(
            set(portfolio["integration_record_ids"]),
            {timing_record.record_id, power_record.record_id},
        )
        self.assertEqual(portfolio["records"][0]["qor_island"], "timing")

    def test_epd_role_portfolio_admits_promising_records_to_integration_and_enhancement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            hypothesis = Hypothesis(
                "promising", "timing", "claim", ("src/rsz/src/Timing.cc",),
                ("timing_examined",), ("timing_card",), "timing",
            )
            record = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_1", hypothesis,
                    {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0},
                    {"timing_examined": 1.0},
                    [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                    "+++ b/src/rsz/src/Timing.cc\n+change\n", "source",
                ),
                verdict=EvidenceVerdict("verified_qor_unattributed", 0.1, 0.4, True, True, ()),
            )
            portfolio = epd.role_portfolio(contract=self.contract, parent=self.parent)
        self.assertEqual(record.epd_status, "promising")
        self.assertEqual([row["record_id"] for row in portfolio["records"]], [record.record_id])
        self.assertEqual(portfolio["enhancement_record_ids"], [record.record_id])

    def test_epd_v2_teacher_idea_is_pending_then_updated_by_its_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            idea_id = epd.register_teacher_ideas(
                round_index=1,
                parent=self.parent,
                evolution_ideas=("Rank timing candidates with a bounded endpoint guard.",),
                diagnosis_summary="Timing is the active residual.",
                parent_policy="Preserve the checked parent.",
            )[0]
            pending = epd.idea(idea_id)
            self.assertEqual(pending["status"], "pending")
            self.assertFalse(pending["executed"])
            self.assertEqual(pending["execution_count"], 0)
            hypothesis = Hypothesis(
                "r1_student_1_endpoint_guard",
                "endpoint_guard",
                "Rank timing candidates with a bounded endpoint guard.",
                ("src/rsz/src/Timing.cc",),
                ("endpoint_examined",),
                ("endpoint_guard",),
                "endpoint_guard",
                student_id="student_1",
                epd_idea_id=idea_id,
            )
            record = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_1", hypothesis,
                    {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0},
                    {"endpoint_examined": 1.0},
                    [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                    "+++ b/src/rsz/src/Timing.cc\n+endpoint guard\n",
                    "endpoint-source",
                ),
                verdict=EvidenceVerdict("validated", 0.1, 0.4, True, True, ()),
            )
            updated = epd.idea(idea_id)
            attempt = epd.records()[0]
            attempt_idea_id = attempt["idea_id"]
        self.assertEqual(updated["status"], "validated")
        self.assertTrue(updated["executed"])
        self.assertEqual(updated["execution_count"], 1)
        self.assertEqual(updated["attempt_ids"], [record.record_id])
        self.assertEqual(attempt_idea_id, idea_id)
        self.assertEqual(attempt["source_change_bundle"]["modified_files"], ["src/rsz/src/Timing.cc"])
        self.assertIn("added:endpoint guard", attempt["source_change_bundle"]["added_mechanism_changes"])

    def test_teacher_can_retire_unexecuted_pending_idea_without_deleting_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            idea_id = epd.register_teacher_ideas(
                round_index=2,
                parent=self.parent,
                evolution_ideas=(
                    {
                        "idea": "An obsolete pending endpoint experiment.",
                        "source_hooks": ("src/rsz/src/Timing.cc",),
                        "expected_signals": ("endpoint_examined",),
                    },
                ),
            )[0]
            retired = epd.retire_pending_ideas(idea_ids=(idea_id,), reason="teacher_retired_pending")
            idea = epd.idea(idea_id)
        self.assertEqual(retired, (idea_id,))
        self.assertEqual(idea["status"], "invalid")
        self.assertEqual(idea["terminal_reason"], "teacher_retired_pending")
        self.assertEqual(idea["execution_count"], 0)

    def test_engine_persists_teacher_ideas_before_student_execution(self) -> None:
        class TwoIdeaTeacher:
            name = "two_idea_teacher"

            def plan(self, *, diagnosis, fallback, **_):
                from goalevolve.agents.markdown_protocol import render_teacher_plan
                from goalevolve.agents.teacher import TeacherPlan, _assignment_from_hypothesis

                markdown = render_teacher_plan(
                    diagnosis_summary="Timing residual remains active.",
                    parent_policy="Keep the checked parent.",
                    evolution_ideas=(
                        {
                            "reference": "idea_1",
                            "idea": "Selected bounded timing guard.",
                            "predicted_stage_effect": "Reduce timing residual after repair.",
                            "source_hooks": ("src/rsz/src/Timing.cc",),
                            "expected_signals": ("guard",),
                            "priority": 0,
                        },
                        {
                            "reference": "idea_2",
                            "idea": "Unselected power-aware endpoint tie-break.",
                            "predicted_stage_effect": "Improve power selection without widening scope.",
                            "source_hooks": ("src/rsz/src/Timing.cc",),
                            "expected_signals": ("guard",),
                            "priority": 1,
                        },
                    ),
                    assignments=[{**_assignment_from_hypothesis(fallback[0]), "idea_reference": "idea_1"}],
                )
                parsed = __import__("goalevolve.agents.markdown_protocol", fromlist=["parse_teacher_plan"]).parse_teacher_plan(markdown)
                selected = CodexTeacher._sanitize_hypotheses(
                    parsed["assignments"], fallback, teacher_context=__import__(
                        "goalevolve.agents.teacher", fromlist=["_teacher_context"]
                    )._teacher_context(parsed)
                )
                return TeacherPlan(tuple(selected), diagnosis, {"teacher_markdown": markdown, "parsed_markdown": parsed, "hypotheses": [item.to_dict() for item in selected]}, {})

            def review(self, **_):
                return {}

        class OneCardPlanner:
            name = "one_card_planner"

            def plan(self, **_):
                return [
                    Hypothesis(
                        "r1_student_1_guard", "guard", "Selected bounded timing guard.",
                        ("src/rsz/src/Timing.cc",), ("guard",), ("guard",), "guard",
                        student_id="student_1",
                    )
                ]

        class StaticEvaluator:
            name = "static"

            def evaluate(self, *, hypothesis, student_id, **_):
                return CandidateResult(
                    student_id, hypothesis, {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0},
                    {"guard": 1.0}, [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                    "+++ b/src/rsz/src/Timing.cc\n+guard\n", "guard-source",
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = GoalEvolveEngine(
                self.contract, root, OneCardPlanner(), StaticEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion(),
                student_ids=("student_1",), teacher=TwoIdeaTeacher(),
            )
            engine.initialize(baseline_metrics=dict(self.parent.metrics))
            engine.run(rounds=1)
            epd = EvolutionProgramDatabase(root)
            ideas = epd.ideas()
            attempts = epd.records()
        by_text = {str(idea["idea"]): idea for idea in ideas}
        self.assertEqual(by_text["Selected bounded timing guard."]["status"], "validated")
        self.assertEqual(by_text["Selected bounded timing guard."]["execution_count"], 1)
        self.assertEqual(by_text["Selected bounded timing guard."]["predicted_stage_effect"], "Reduce timing residual after repair.")
        self.assertTrue(by_text["Selected bounded timing guard."]["inherited"])
        self.assertEqual(by_text["Selected bounded timing guard."]["inherited_parent_id"], "round_001:student_1")
        self.assertEqual(by_text["Unselected power-aware endpoint tie-break."]["status"], "pending")
        self.assertEqual(by_text["Unselected power-aware endpoint tie-break."]["execution_count"], 0)
        self.assertEqual(attempts[1]["idea_id"], by_text["Selected bounded timing guard."]["idea_id"])

    def test_epd_v2_integrator_uses_only_validated_records_not_inherited_by_current_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            validated = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_1",
                    Hypothesis("timing", "timing", "timing", ("src/rsz/src/Timing.cc",), ("timing",), ("timing",), "timing"),
                    {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0}, {"timing": 1.0}, checks,
                    "+++ b/src/rsz/src/Timing.cc\n+timing\n", "timing-source",
                ),
                verdict=EvidenceVerdict("validated", 0.1, 0.4, True, True, ()),
            )
            second_validated = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_3",
                    Hypothesis("route", "route", "route", ("src/grt/src/Route.cc",), ("route",), ("route",), "route"),
                    {"tns_abs_ns": 70.0, "leakage_power_pw": 190.0}, {"route": 1.0}, checks,
                    "+++ b/src/grt/src/Route.cc\n+route\n", "route-source",
                ),
                verdict=EvidenceVerdict("validated", 0.1, 0.2, True, True, ()),
            )
            promising = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_2",
                    Hypothesis("power", "power", "power", ("src/rsz/src/Power.cc",), ("power",), ("power",), "power"),
                    {"tns_abs_ns": 100.0, "leakage_power_pw": 170.0}, {}, checks,
                    "+++ b/src/rsz/src/Power.cc\n+power\n", "power-source",
                ),
                verdict=EvidenceVerdict("verified_qor_unattributed", 0.1, 0.3, True, True, ()),
            )
            epd.mark_inherited(record_id=validated.record_id, parent=self.parent)
            portfolio = epd.role_portfolio(contract=self.contract, parent=self.parent)
        integration_ids = {record_id for pair in portfolio["integration_candidates"] for record_id in pair}
        self.assertNotIn(validated.record_id, integration_ids)
        self.assertNotIn(promising.record_id, integration_ids)
        self.assertNotIn(second_validated.record_id, integration_ids)
        self.assertEqual(portfolio["integration_candidates"], [])
        self.assertEqual(portfolio["enhancement_candidates"], [promising.record_id])

    def test_epd_v2_every_uninherited_validated_attempt_is_represented_in_an_integrator_option(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            records = []
            for index in range(13):
                hypothesis = Hypothesis(
                    f"timing_{index}",
                    "timing",
                    f"timing mechanism {index}",
                    (f"src/rsz/src/Timing{index}.cc",),
                    (f"timing_{index}",),
                    (f"timing_{index}",),
                    f"timing_{index}",
                )
                records.append(
                    epd.record(
                        round_index=1,
                        parent=self.parent,
                        candidate=CandidateResult(
                            f"student_{index}",
                            hypothesis,
                            {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0},
                            {f"timing_{index}": 1.0},
                            checks,
                            f"+++ b/src/rsz/src/Timing{index}.cc\n+guard {index}\n",
                            f"timing-source-{index}",
                        ),
                        verdict=EvidenceVerdict("validated", 0.1, 0.2, True, True, ()),
                    )
                )
            portfolio = epd.role_portfolio(contract=self.contract, parent=self.parent)

        represented = {
            record_id
            for candidate in portfolio["integration_candidates"]
            for record_id in candidate
        }
        self.assertEqual(represented, {record.record_id for record in records})

    def test_epd_v2_planner_keeps_every_uninherited_validated_record_selectable_for_integration(self) -> None:
        cards = tuple(
            MechanismCard(
                f"timing_{index}",
                "timing",
                ("tns",),
                (f"src/rsz/src/Timing{index}.cc",),
                (f"timing_{index}",),
                f"Fresh timing mechanism {index}.",
            )
            for index in range(13)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            record_ids = set()
            for index, card in enumerate(cards):
                record = epd.record(
                    round_index=1,
                    parent=self.parent,
                    candidate=CandidateResult(
                        f"student_{index}",
                        Hypothesis(
                            f"timing_{index}", "timing", f"timing mechanism {index}",
                            card.source_hooks, card.expected_signals, (card.card_id,), card.card_id,
                        ),
                        {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0},
                        {card.expected_signals[0]: 1.0}, checks,
                        f"+++ b/{card.source_hooks[0]}\n+guard {index}\n", f"source-{index}",
                    ),
                    verdict=EvidenceVerdict("validated", 0.1, 0.2, True, True, ()),
                )
                record_ids.add(record.record_id)
            plan = DiversePlanner(DiverseRetriever(cards)).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=2,
                student_ids=("student_1", "student_2", "student_3", "student_4"),
                state_root=root,
            )

        selectable = {
            record_id
            for option in plan[2].candidate_options
            for record_id in option["epd_record_ids"]
        }
        self.assertEqual(selectable, record_ids)

    def test_epd_v2_enhancement_budget_is_bounded_per_promising_idea(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root, max_reinforcement_attempts=2)
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            seed = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_1",
                    Hypothesis("seed", "timing", "seed", ("src/rsz/src/Timing.cc",), ("timing",), ("seed",), "seed"),
                    {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0}, {}, checks,
                    "+++ b/src/rsz/src/Timing.cc\n+seed\n", "seed-source",
                ),
                verdict=EvidenceVerdict("verified_qor_unattributed", 0.1, 0.3, True, True, ()),
            )
            seed_idea_id = str(epd.records()[0]["idea_id"])
            for attempt in (1, 2):
                epd.record(
                    round_index=attempt + 1,
                    parent=self.parent,
                    candidate=CandidateResult(
                        f"student_{attempt + 1}",
                        Hypothesis(
                            f"enhance_{attempt}", "enhancement", "enhance", ("src/rsz/src/Timing.cc",),
                            ("timing",), ("enhance",), "enhance", student_role="enhancer",
                            role_mode="epd_enhancement", epd_record_ids=(seed.record_id,),
                        ),
                        {"tns_abs_ns": 100.0, "leakage_power_pw": 200.0}, {"timing": 1.0}, checks,
                        f"+++ b/src/rsz/src/Timing.cc\n+enhance {attempt}\n", f"enhance-source-{attempt}",
                    ),
                    verdict=EvidenceVerdict("invalid", 0.0, 0.0, True, False, ()),
                )
            seed_idea = epd.idea(seed_idea_id)
        self.assertEqual(seed_idea["reinforcement_attempts"], 2)
        self.assertEqual(seed_idea["status"], "invalid")
        self.assertEqual(seed_idea["terminal_reason"], "reinforcement_budget_exhausted")

    def test_epd_v2_uses_the_configured_reinforcement_budget_for_new_ideas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            epd = EvolutionProgramDatabase(Path(temporary), max_reinforcement_attempts=1)
            idea_id = epd.register_teacher_ideas(
                round_index=1,
                parent=self.parent,
                evolution_ideas=(
                    {
                        "idea": "Refine one bounded timing guard.",
                        "source_hooks": ("src/rsz/src/Timing.cc",),
                        "expected_signals": ("timing_examined",),
                    },
                ),
            )[0]
            budget = epd.idea(idea_id)["max_reinforcement_attempts"]

        self.assertEqual(budget, 1)

    def test_epd_v2_pending_teacher_idea_is_selectable_by_an_explorer(self) -> None:
        card = MechanismCard(
            "fresh_timing",
            "timing",
            ("tns",),
            ("src/rsz/src/Timing.cc",),
            ("timing_examined",),
            "Fresh timing exploration.",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            idea_id = epd.register_teacher_ideas(
                round_index=1,
                parent=self.parent,
                evolution_ideas=(
                    {
                        "idea": "Retry one bounded endpoint guard.",
                        "source_hooks": ("src/rsz/src/Timing.cc",),
                        "expected_signals": ("endpoint_guard_examined",),
                        "priority": 0,
                    },
                ),
            )[0]
            plan = DiversePlanner(DiverseRetriever((card,))).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=2,
                student_ids=("student_1",),
                state_root=root,
            )

        options = plan[0].candidate_options
        pending = next(option for option in options if option["epd_idea_id"] == idea_id)
        self.assertEqual(pending["role_mode"], "pending_exploration")
        self.assertEqual(pending["expected_signals"], ("endpoint_guard_examined",))

    def test_epd_portfolio_records_descendant_feedback_for_elite_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            first = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_1",
                    Hypothesis("first", "timing", "first", ("src/rsz/src/Timing.cc",), ("timing",), ("first",), "first"),
                    {"tns_abs_ns": 70.0, "leakage_power_pw": 200.0}, {"timing": 1.0}, checks,
                    "+++ b/src/rsz/src/Timing.cc\n+first\n", "first-source",
                ),
                verdict=EvidenceVerdict("validated", 0.2, 0.2, True, True, ()),
            )
            second = epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_2",
                    Hypothesis("second", "power", "second", ("src/rsz/src/Power.cc",), ("power",), ("second",), "second"),
                    {"tns_abs_ns": 100.0, "leakage_power_pw": 180.0}, {"power": 1.0}, checks,
                    "+++ b/src/rsz/src/Power.cc\n+second\n", "second-source",
                ),
                verdict=EvidenceVerdict("validated", 0.1, 0.2, True, True, ()),
            )
            epd.record(
                round_index=2,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_3",
                    Hypothesis(
                        "child", "integration", "child", ("src/rsz/src/Timing.cc", "src/rsz/src/Power.cc"),
                        ("timing", "power"), ("child",), "child", student_role="integrator",
                        role_mode="epd_integration", epd_record_ids=(first.record_id, second.record_id),
                    ),
                    {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0}, {"timing": 1.0, "power": 1.0}, checks,
                    "+++ b/src/rsz/src/Timing.cc\n+child\n", "child-source",
                ),
                verdict=EvidenceVerdict("validated", 0.05, 0.5, True, True, ()),
            )
            portfolio = epd.role_portfolio(contract=self.contract, parent=self.parent)
        rows = {row["record_id"]: row for row in portfolio["records"]}
        self.assertEqual(rows[first.record_id]["offspring_validated_count"], 1)
        self.assertEqual(rows[second.record_id]["offspring_validated_count"], 1)
        self.assertGreater(rows[first.record_id]["elite_score"], rows[first.record_id]["distance_gain"])

    def test_diverse_planner_uses_only_eligible_epd_roles(self) -> None:
        cards = (
            MechanismCard("explore_timing", "timing", ("tns",), ("src/rsz/src/Timing.cc",), ("timing_examined",), "Explore timing."),
            MechanismCard("explore_power", "power", ("leakage",), ("src/rsz/src/Power.cc",), ("power_examined",), "Explore power."),
            MechanismCard("explore_route", "route", ("tns",), ("src/grt/src/Route.cc",), ("route_examined",), "Explore route."),
            MechanismCard("explore_setup", "setup", ("tns",), ("src/rsz/src/Setup.cc",), ("setup_examined",), "Explore setup."),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            for student_id, hypothesis, metrics in (
                ("student_1", Hypothesis("old_timing", "timing", "old timing", ("src/rsz/src/Timing.cc",), ("timing_examined",), ("timing_card",), "old:timing"), {"tns_abs_ns": 60.0, "leakage_power_pw": 200.0}),
                ("student_2", Hypothesis("old_power", "power", "old power", ("src/rsz/src/Power.cc",), ("power_retained",), ("power_card",), "old:power"), {"tns_abs_ns": 100.0, "leakage_power_pw": 170.0}),
            ):
                epd.record(
                    round_index=1,
                    parent=self.parent,
                    candidate=CandidateResult(student_id, hypothesis, metrics, {hypothesis.expected_signals[0]: 1.0}, checks, f"+++ b/{hypothesis.source_hooks[0]}\n+change\n", f"{student_id}-source"),
                    verdict=EvidenceVerdict("validated", 0.1, 0.2, True, True, ()),
                )
            plan = DiversePlanner(DiverseRetriever(cards)).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=2,
                student_ids=("student_1", "student_2", "student_3", "student_4"),
                state_root=root,
            )
        self.assertEqual([item.student_role for item in plan], ["explorer", "explorer", "integrator"])
        self.assertEqual(len(plan[2].epd_record_ids), 2)
        self.assertIn("epd_integration", plan[2].role_mode)

    def test_student_packet_injects_role_specific_epd_evidence(self) -> None:
        from goalevolve.agents.prompting import student_packet

        hypothesis = Hypothesis(
            "integrate", "integration", "combine validated changes",
            ("src/rsz/src/Timing.cc", "src/rsz/src/Power.cc"),
            ("timing_examined", "power_retained"), ("epd_integration",), "integration",
            student_role="integrator",
            role_mode="epd_integration",
            epd_record_ids=("EPD_timing", "EPD_power"),
        )
        packet = student_packet(
            parent=self.parent,
            hypothesis=hypothesis,
            prior=(),
            epd_records=(
                {"record_id": "EPD_timing", "implementation_diff_artifact": "/tmp/timing.diff"},
                {"record_id": "EPD_power", "implementation_diff_artifact": "/tmp/power.diff"},
            ),
        )
        self.assertIn("## Integrator Operating Protocol", packet)
        self.assertIn("EPD_timing", packet)
        self.assertIn("/tmp/power.diff", packet)
        self.assertIn("Do not concatenate", packet)

    def test_enhancer_packet_routes_prior_source_change_through_artifact_path(self) -> None:
        from goalevolve.agents.prompting import student_packet

        hypothesis = Hypothesis(
            "enhance", "enhancement", "refine prior timing change",
            ("src/rsz/src/Timing.cc",), ("timing_examined",), ("enhance",), "enhance",
            student_role="enhancer", role_mode="epd_enhancement", epd_record_ids=("EPD_promising",),
        )
        packet = student_packet(
            parent=self.parent,
            hypothesis=hypothesis,
            prior=(),
            epd_records=(
                {
                    "record_id": "EPD_promising",
                    "epd_status": "promising",
                    "source_change_bundle": {
                        "modified_files": ["src/rsz/src/Timing.cc"],
                        "added_code": ["add bounded endpoint guard"],
                        "removed_code": ["remove unguarded selection"],
                        "added_mechanism_changes": ["added:endpoint_guard"],
                        "removed_mechanism_changes": ["removed:unguarded_selection"],
                        "telemetry_changes": ["added:METRIC|timing_examined"],
                    },
                    "implementation_diff_artifact": "/tmp/promising.diff",
                },
            ),
        )
        self.assertIn("## Enhancer Candidate Directory", packet)
        self.assertIn("## Previous-round Enhancer Dossier", packet)
        self.assertIn("/tmp/promising.diff", packet)
        self.assertNotIn("add bounded endpoint guard", packet)
        self.assertNotIn("remove unguarded selection", packet)
        self.assertNotIn("added:endpoint_guard", packet)
        self.assertNotIn("removed:unguarded_selection", packet)
        self.assertNotIn("added:METRIC|timing_examined", packet)

    def test_student_packets_route_epd_evidence_by_role(self) -> None:
        from goalevolve.agents.prompting import student_packet

        with tempfile.TemporaryDirectory() as temporary:
            epd_root = Path(temporary) / "knowledge" / "epd"
            mechanism_path = epd_root / "mechanisms" / "MECH_demo" / "mechanism_card.json"
            atomic_json(
                mechanism_path,
                {
                    "mechanism_id": "MECH_demo",
                    "attempt_ids": ["EPD_promising", "EPD_validated"],
                    "status": "promising",
                    "mechanism_summary": "Bound endpoint candidate admission.",
                    "decision_boundary": "candidate admission",
                    "source_hooks": ["src/rsz/src/Timing.cc"],
                    "state_read_set": ["endpoint slack"],
                    "source_write_set": ["candidate admission"],
                    "action_type": "candidate_filter",
                    "commit_scope": "candidate",
                    "dependencies": [],
                    "known_conflicts": ["shared ranking guard"],
                    "parent_compatibility": ["baseline"],
                    "observed_qor_effects": [],
                    "downstream_retention": [],
                    "student_reflection_paths": [str(epd_root / "attempts" / "EPD_promising" / "student_reflection.md")],
                    "implementation_artifact_paths": [str(epd_root / "attempts" / "EPD_promising" / "implementation.diff")],
                },
            )
            atomic_json(epd_root / "manifest.json", {"mechanisms": [{"mechanism_id": "MECH_demo", "path": str(mechanism_path), "status": "promising"}]})
            records = (
                {
                    "record_id": "EPD_promising",
                    "idea_id": "IDEA_promising",
                    "epd_status": "promising",
                    "metrics": {"tns_abs_ns": 99.0},
                    "phase_signals": {"endpoint_examined": 1.0},
                    "source_hooks": ("src/rsz/src/Timing.cc",),
                    "source_change_bundle": {"added_code": ["do not inline this source"]},
                },
                {
                    "record_id": "EPD_validated",
                    "idea_id": "IDEA_validated",
                    "epd_status": "validated",
                    "metrics": {"tns_abs_ns": 98.0},
                    "phase_signals": {"endpoint_examined": 2.0},
                    "source_hooks": ("src/rsz/src/Timing.cc",),
                },
            )
            explorer = replace(self.hypothesis, student_id="student_1", student_role="explorer", epd_idea_id="IDEA_new")
            enhancer = replace(self.hypothesis, student_id="student_2", student_role="enhancer", role_mode="epd_enhancement", epd_record_ids=("EPD_promising",))
            integrator = replace(self.hypothesis, student_id="student_3", student_role="integrator", role_mode="epd_integration", epd_record_ids=("EPD_promising", "EPD_validated"))
            explorer_packet = student_packet(
                parent=self.parent,
                hypothesis=explorer,
                prior=(),
                epd_root=epd_root,
                idea_record={"draft_signature_id": "draft_1", "epd_search_query": "draft_1", "retrieved_historical_ideas": ["IDEA_old"], "opened_epd_records": ["IDEA_old"], "nearest_historical_idea": "IDEA_old", "novelty_conclusion": "different decision state"},
            )
            enhancer_packet = student_packet(parent=self.parent, hypothesis=enhancer, prior=(), epd_records=records, epd_root=epd_root)
            integrator_packet = student_packet(parent=self.parent, hypothesis=integrator, prior=(), epd_records=records, epd_root=epd_root)

        self.assertIn("## Explorer Novelty Obligations", explorer_packet)
        self.assertIn("draft_1", explorer_packet)
        self.assertNotIn("EPD_promising", explorer_packet)
        self.assertIn("## Enhancer Candidate Directory", enhancer_packet)
        self.assertIn("student_reflection.md", enhancer_packet)
        self.assertNotIn("do not inline this source", enhancer_packet)
        self.assertIn("## Integrator Compatibility Directory", integrator_packet)
        self.assertIn("source_write_set", integrator_packet)
        self.assertIn("shared ranking guard", integrator_packet)
        for packet in (explorer_packet, enhancer_packet, integrator_packet):
            self.assertIn("## Student Reflection", packet)

    def test_enhancer_packet_routes_mechanism_card_and_compact_evidence(self) -> None:
        from goalevolve.agents.prompting import student_packet

        with tempfile.TemporaryDirectory() as temporary:
            epd_root = Path(temporary) / "knowledge" / "epd"
            mechanism_path = epd_root / "mechanisms" / "MECH_promising" / "mechanism_card.json"
            atomic_json(
                mechanism_path,
                {
                    "mechanism_id": "MECH_promising",
                    "attempt_ids": ["EPD_promising"],
                    "status": "promising",
                },
            )
            atomic_json(
                epd_root / "manifest.json",
                {"mechanisms": [{"mechanism_id": "MECH_promising", "path": str(mechanism_path), "status": "promising"}]},
            )
            enhancer = replace(
                self.hypothesis,
                student_role="enhancer",
                role_mode="epd_enhancement",
                epd_record_ids=("EPD_promising",),
            )
            packet = student_packet(
                parent=self.parent,
                hypothesis=enhancer,
                prior=(),
                epd_root=epd_root,
                epd_records=(
                    {
                        "record_id": "EPD_promising",
                        "idea_id": "IDEA_promising",
                        "epd_status": "promising",
                        "evidence_state": "verified_qor_unattributed",
                        "distance_gain": 0.25,
                        "metrics": {"full_metric_must_stay_path_addressable": 123.0},
                        "phase_signals": {"full_signal_must_stay_path_addressable": 1.0},
                        "expected_signals": ("expected_signal",),
                    },
                ),
            )

        self.assertIn(str(mechanism_path), packet)
        self.assertIn('"evidence_state": "verified_qor_unattributed"', packet)
        self.assertIn('"activation_summary"', packet)
        self.assertNotIn("full_metric_must_stay_path_addressable", packet)
        self.assertNotIn("full_signal_must_stay_path_addressable", packet)

    def test_enhancer_dossier_excludes_unselected_eligible_record(self) -> None:
        from goalevolve.agents.prompting import student_packet

        enhancer = replace(
            self.hypothesis,
            student_role="enhancer",
            role_mode="epd_enhancement",
            epd_record_ids=("EPD_selected",),
        )
        packet = student_packet(
            parent=self.parent,
            hypothesis=enhancer,
            prior=(),
            epd_records=(
                {
                    "record_id": "EPD_selected",
                    "epd_status": "promising",
                    "source_change_bundle": {"prior_claim": "selected dossier claim"},
                },
                {
                    "record_id": "EPD_unrelated",
                    "epd_status": "promising",
                    "source_change_bundle": {"prior_claim": "unrelated dossier claim"},
                },
            ),
            enhancement_eligible_record_ids=("EPD_selected", "EPD_unrelated"),
        )

        self.assertIn("EPD_unrelated", packet)
        self.assertIn("selected dossier claim", packet)
        self.assertNotIn("unrelated dossier claim", packet)

    def test_integrator_packet_uses_only_controller_eligible_mechanisms(self) -> None:
        from goalevolve.agents.prompting import student_packet

        with tempfile.TemporaryDirectory() as temporary:
            epd_root = Path(temporary) / "knowledge" / "epd"
            cards = {
                "MECH_valid": ("EPD_valid", "validated", "large_valid_metric"),
                "MECH_promising": ("EPD_promising", "promising", "large_promising_metric"),
                "MECH_inherited": ("EPD_inherited", "validated", "large_inherited_metric"),
            }
            manifest_rows = []
            for mechanism_id, (record_id, status, metric) in cards.items():
                mechanism_path = epd_root / "mechanisms" / mechanism_id / "mechanism_card.json"
                atomic_json(
                    mechanism_path,
                    {
                        "mechanism_id": mechanism_id,
                        "attempt_ids": [record_id],
                        "status": status,
                        "mechanism_summary": mechanism_id,
                        "observed_qor_effects": [{"record_id": record_id, "metrics": {metric: 1.0}, "distance_gain": 0.1}],
                        "downstream_retention": [{"record_id": record_id, "checkpoint_effects": {metric: {"tns_abs_ns": 1.0}}}],
                    },
                )
                manifest_rows.append({"mechanism_id": mechanism_id, "path": str(mechanism_path), "status": status})
            atomic_json(epd_root / "manifest.json", {"mechanisms": manifest_rows})
            integrator = replace(
                self.hypothesis,
                student_role="integrator",
                role_mode="epd_integration",
                epd_record_ids=("EPD_valid",),
            )
            packet = student_packet(
                parent=self.parent,
                hypothesis=integrator,
                prior=(),
                epd_root=epd_root,
                epd_records=(
                    {"record_id": "EPD_valid", "epd_status": "validated"},
                    {"record_id": "EPD_promising", "epd_status": "promising"},
                    {"record_id": "EPD_inherited", "epd_status": "validated"},
                ),
                integration_eligible_record_ids=("EPD_valid",),
            )

        self.assertIn("MECH_valid", packet)
        self.assertNotIn("MECH_promising", packet)
        self.assertNotIn("MECH_inherited", packet)
        self.assertIn('"qor_summary"', packet)
        self.assertIn('"downstream_retention_summary"', packet)
        self.assertNotIn("large_valid_metric", packet)

    def test_student_reflection_contract_requires_bounded_avoidance_recommendation(self) -> None:
        from goalevolve.agents.prompting import student_packet

        packet = student_packet(parent=self.parent, hypothesis=self.hypothesis, prior=())

        self.assertIn("avoid-next-time mechanism", packet)
        self.assertIn("exactly one of validated|promising|invalid|unactivated", packet)
        self.assertIn("Controller alone decides promotion", packet)

    def test_teacher_markdown_handoff_is_compiled_into_the_student_packet(self) -> None:
        from goalevolve.agents.prompting import student_packet

        slot = Hypothesis(
            "r2_student_1_timing", "timing", "default timing claim",
            ("src/rsz/src/Timing.cc",), ("timing_examined",), ("timing_card",), "timing",
            student_id="student_1",
        )
        selected = CodexTeacher._sanitize_hypotheses(
            (
                {
                    "student_id": "student_1",
                    "idea_reference": "idea_1",
                    "claim": "Prefer endpoint ranking with a bounded stale-cache guard.",
                    "selection_rationale": "Timing debt dominates and this hook has not been refuted.",
                },
            ),
            (slot,),
            teacher_context={
                "diagnosis_summary": "TNS is the sole unresolved target.",
                "parent_policy": "Preserve the checked low-power parent.",
                "evolution_ideas": (
                    "Use endpoint freshness to avoid stale criticality ordering.",
                    "Keep rollback accounting explicit.",
                ),
                "idea_records": {
                    "idea_1": {"predicted_stage_effect": "Reduce timing debt after the repair stage."},
                },
            },
        )
        packet = student_packet(parent=self.parent, hypothesis=selected[0], prior=())
        self.assertIn("## Teacher Handoff", packet)
        self.assertIn("TNS is the sole unresolved target.", packet)
        self.assertIn("Preserve the checked low-power parent.", packet)
        self.assertIn("endpoint freshness", packet)
        self.assertIn("Timing debt dominates", packet)
        self.assertIn("Reduce timing debt after the repair stage.", packet)

    def test_teacher_internal_cpp_schedule_suggestion_reaches_student_as_advisory(self) -> None:
        from goalevolve.agents.markdown_protocol import parse_teacher_plan
        from goalevolve.agents.prompting import student_packet

        parsed = parse_teacher_plan(
            """## Diagnosis Summary
Timing is the active residual.

## Evolution Ideas
### idea_1
- Idea: Use a bounded late timing-recovery phase.
- Internal C++ Scheduling Suggestion: Consider invoking the existing bounded policy after repair_power only when its journal guard admits the phase; do not edit Tcl.

## Parent Policy
Keep the checked parent.

## Student Assignments
### student_1
- Role: explorer
- Claim: Use a bounded late timing-recovery phase.
- Selection Rationale: The phase may recover the observed debt.
- Source Hooks: src/rsz/src/Timing.cc
- Expected Signals: timing_recovery_examined
- Source Evidence: src/rsz/src/Timing.cc::adjustTiming
- Falsification Condition: No final timing recovery under the frozen contract.
- Internal C++ Scheduling Suggestion: Consider invoking the existing bounded policy after repair_power only when its journal guard admits the phase; do not edit Tcl.
- EPD References: none
"""
        )
        self.assertEqual(
            parsed["evolution_idea_records"][0]["internal_cpp_scheduling_suggestion"],
            "Consider invoking the existing bounded policy after repair_power only when its journal guard admits the phase; do not edit Tcl.",
        )
        self.assertEqual(
            parsed["assignments"][0]["internal_cpp_scheduling_suggestion"],
            "Consider invoking the existing bounded policy after repair_power only when its journal guard admits the phase; do not edit Tcl.",
        )
        hypothesis = Hypothesis(
            "schedule", "timing", "bounded timing recovery",
            ("src/rsz/src/Timing.cc",), ("timing_recovery_examined",), ("card",), "schedule",
            teacher_internal_cpp_scheduling_suggestion=parsed["assignments"][0]["internal_cpp_scheduling_suggestion"],
        )
        packet = student_packet(parent=self.parent, hypothesis=hypothesis, prior=())
        self.assertIn("## Internal C++ Scheduling Suggestion", packet)
        self.assertIn("you may accept, adapt, or reject this advisory suggestion", packet)
        self.assertIn("do not edit Tcl, SDC, design, or benchmark inputs", packet)

    def test_student_scheduling_decision_is_persisted_as_advisory_artifact(self) -> None:
        from goalevolve.agents.codex_student import CodexStudentEditor

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            message = root / "last_message.md"
            message.write_text(
                """Implemented the bounded phase.
- Decision: adapted
- Rationale: The current Optimizer dispatch already invokes the policy once, so I tightened its existing guard instead of adding a second invocation.
""",
                encoding="utf-8",
            )
            path = CodexStudentEditor._write_internal_cpp_scheduling_decision(
                artifact_root=root,
                last_message=message,
                suggestion="Invoke the bounded policy after repair_power.",
            )
            decision = load_json(path)
        self.assertEqual(decision["decision"], "adapted")
        self.assertIn("already invokes the policy once", decision["rationale"])
        self.assertEqual(decision["suggestion"], "Invoke the bounded policy after repair_power.")
        self.assertTrue(decision["advisory_only"])

    def test_epd_roles_offer_teacher_selectable_verified_crossovers(self) -> None:
        cards = (
            MechanismCard("explore_timing", "timing", ("tns",), ("src/rsz/src/Timing.cc",), ("timing_examined",), "Explore timing."),
            MechanismCard("explore_power", "power", ("leakage",), ("src/rsz/src/Power.cc",), ("power_examined",), "Explore power."),
            MechanismCard("explore_route", "route", ("tns",), ("src/grt/src/Route.cc",), ("route_examined",), "Explore route."),
            MechanismCard("explore_setup", "setup", ("tns",), ("src/rsz/src/Setup.cc",), ("setup_examined",), "Explore setup."),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            seeds = (
                ("timing", "src/rsz/src/Timing.cc", "timing_examined", {"tns_abs_ns": 70.0, "leakage_power_pw": 200.0}),
                ("power", "src/rsz/src/Power.cc", "power_examined", {"tns_abs_ns": 100.0, "leakage_power_pw": 170.0}),
                ("route", "src/grt/src/Route.cc", "route_examined", {"tns_abs_ns": 80.0, "leakage_power_pw": 190.0}),
            )
            for index, (family, hook, signal, metrics) in enumerate(seeds, start=1):
                hypothesis = Hypothesis(
                    f"old_{family}", family, f"old {family}", (hook,), (signal,), (f"{family}_card",), family,
                )
                epd.record(
                    round_index=1,
                    parent=self.parent,
                    candidate=CandidateResult(
                        f"student_{index}", hypothesis, metrics, {signal: 1.0}, checks,
                        f"+++ b/{hook}\n+change\n", f"{family}-source",
                    ),
                    verdict=EvidenceVerdict("validated", 0.1, 0.2, True, True, ()),
                )
            plan = DiversePlanner(DiverseRetriever(cards)).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=2,
                student_ids=("student_1", "student_2", "student_3", "student_4"),
                state_root=root,
            )
        integrator = plan[2]
        self.assertGreaterEqual(len(integrator.candidate_options), 2)
        integration_options = [Hypothesis(**option) for option in integrator.candidate_options]
        self.assertTrue(all(option.student_role == "integrator" for option in integration_options))
        self.assertTrue(all(len(option.epd_record_ids) == 2 for option in integration_options))
        selected = CodexTeacher._sanitize_hypotheses(
            ({"student_id": "student_3", "candidate_id": integration_options[-1].hypothesis_id},),
            (integrator,),
        )
        self.assertEqual(selected[0].epd_record_ids, integration_options[-1].epd_record_ids)

    def test_roles_expand_to_four_explorers_before_epd_has_eligible_roles(self) -> None:
        cards = tuple(
            MechanismCard(
                f"card_{index}", f"family_{index}", ("tns",),
                (f"src/rsz/src/F{index}.cc",), (f"signal_{index}",), f"claim {index}",
            )
            for index in range(4)
        )
        with tempfile.TemporaryDirectory() as temporary:
            plan = DiversePlanner(DiverseRetriever(cards)).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=1,
                student_ids=("student_1", "student_2", "student_3", "student_4"),
                state_root=Path(temporary),
            )
        self.assertEqual([item.student_role for item in plan], ["explorer", "explorer", "explorer", "explorer"])
        self.assertTrue(all(item.role_mode == "fresh_exploration" for item in plan))

    def test_one_validated_epd_record_does_not_create_a_bootstrap_role(self) -> None:
        cards = tuple(
            MechanismCard(
                f"card_{index}", f"family_{index}", ("tns",),
                (f"src/rsz/src/F{index}.cc",), (f"signal_{index}",), f"claim {index}",
            )
            for index in range(4)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epd = EvolutionProgramDatabase(root)
            hypothesis = Hypothesis(
                "only_one", "timing", "one", ("src/rsz/src/F0.cc",), ("signal_0",), ("card_0",), "one",
            )
            epd.record(
                round_index=1,
                parent=self.parent,
                candidate=CandidateResult(
                    "student_1", hypothesis, {"tns_abs_ns": 70.0, "leakage_power_pw": 200.0},
                    {"signal_0": 1.0}, [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                    "+++ b/src/rsz/src/F0.cc\n+change\n", "source",
                ),
                verdict=EvidenceVerdict("validated", 0.1, 0.3, True, True, ()),
            )
            plan = DiversePlanner(DiverseRetriever(cards)).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=2,
                student_ids=("student_1", "student_2", "student_3", "student_4"),
                state_root=root,
            )
        self.assertEqual([item.student_role for item in plan], ["explorer", "explorer", "explorer", "explorer"])

    def test_controller_persists_markdown_and_four_role_packets(self) -> None:
        class FourCardPlanner:
            name = "four_card_planner"

            def __init__(self, hypotheses):
                self.hypotheses = hypotheses

            def plan(self, **_):
                return list(self.hypotheses)

            def commit_round(self, **_):
                return None

        class StaticEditor:
            name = "static_editor"

            def apply(self, **_):
                return StudentEditReport(True, "edited", "static", None, {})

            def repair(self, **_):
                return StudentEditReport(False, "not_needed", "static", None, {})

        class StaticEvaluator:
            name = "static_evaluator"

            def evaluate(self, *, hypothesis, student_id, **_):
                return CandidateResult(
                    student_id,
                    hypothesis,
                    {"tns_abs_ns": 100.0, "leakage_power_pw": 200.0},
                    {signal: 1.0 for signal in hypothesis.expected_signals},
                    [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                    f"+++ b/{hypothesis.source_hooks[0]}\n+change\n",
                    f"commit-{student_id}",
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hypotheses = tuple(
                Hypothesis(
                    f"r1_student_{index}_card_{index}",
                    f"family_{index}",
                    f"claim {index}",
                    (f"src/rsz/src/Family{index}.cc",),
                    (f"signal_{index}",),
                    (f"card_{index}",),
                    f"family_{index}",
                    student_role=role,
                    role_mode="epd_integration" if role == "integrator" else ("epd_enhancement" if role == "enhancer" else "fresh_exploration"),
                    epd_record_ids=("EPD_demo",) if role in {"integrator", "enhancer"} else (),
                    student_id=f"student_{index}",
                )
                for index, role in enumerate(("explorer", "explorer", "integrator", "enhancer"), start=1)
            )
            engine = GoalEvolveEngine(
                self.contract,
                root,
                FourCardPlanner(hypotheses),
                StaticEvaluator(),
                IsolatedWorkspace(),
                StrictEvidencePromotion(),
                student_editor=StaticEditor(),
                teacher=__import__("goalevolve.agents.teacher", fromlist=["HeuristicTeacher"]).HeuristicTeacher(),
            )
            engine.initialize(baseline_metrics=dict(self.parent.metrics))
            engine.run(rounds=1)
            round_root = root / "rounds" / "round_001"
            plan = load_json(round_root / "teacher_plan.json")
            self.assertTrue((round_root / "teacher_plan.md").is_file())
            self.assertTrue((round_root / "teacher_plan.parsed.json").is_file())
            self.assertTrue((round_root / "teacher_review.md").is_file())
            self.assertTrue((round_root / "epd_role_portfolio.json").is_file())
            self.assertEqual(
                [item["student_role"] for item in plan["hypotheses"]],
                ["explorer", "explorer", "integrator", "enhancer"],
            )
            self.assertIn("## Explorer Operating Protocol", (round_root / "prompts" / "student_1.md").read_text(encoding="utf-8"))
            self.assertIn("## Integrator Operating Protocol", (round_root / "prompts" / "student_3.md").read_text(encoding="utf-8"))
            self.assertIn("## Enhancer Operating Protocol", (round_root / "prompts" / "student_4.md").read_text(encoding="utf-8"))

    def test_two_round_controller_promotes_validated_epd_into_role_specific_packets(self) -> None:
        class StaticEditor:
            name = "static_editor"

            def apply(self, **_):
                return StudentEditReport(True, "edited", "static", None, {})

            def repair(self, **_):
                return StudentEditReport(False, "not_needed", "static", None, {})

        class ImprovingEvaluator:
            name = "improving_evaluator"

            def evaluate(self, *, parent, hypothesis, student_id, round_index, **_):
                index = int(student_id.rsplit("_", 1)[-1])
                metrics = dict(parent.metrics)
                metrics["tns_abs_ns"] = float(metrics["tns_abs_ns"]) - 1.0 - index / 10.0
                return CandidateResult(
                    student_id,
                    hypothesis,
                    metrics,
                    (
                        {}
                        if round_index == 1 and student_id == "student_2"
                        else {signal: 1.0 for signal in hypothesis.expected_signals}
                    ),
                    [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                    f"+++ b/{hypothesis.source_hooks[0]}\n+// round {round_index} {student_id}\n",
                    f"commit-{round_index}-{student_id}",
                )

        cards = tuple(
            MechanismCard(
                f"card_{index}", f"family_{index}", ("tns",),
                (f"src/rsz/src/F{index}.cc",), (f"signal_{index}",), f"claim {index}",
            )
            for index in range(1, 5)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = GoalEvolveEngine(
                self.contract,
                root,
                DiversePlanner(DiverseRetriever(cards)),
                ImprovingEvaluator(),
                IsolatedWorkspace(),
                StrictEvidencePromotion(),
                student_editor=StaticEditor(),
                teacher=__import__("goalevolve.agents.teacher", fromlist=["HeuristicTeacher"]).HeuristicTeacher(),
            )
            engine.initialize(baseline_metrics=dict(self.parent.metrics))
            engine.run(rounds=2)
            round_two = root / "rounds" / "round_002"
            plan = load_json(round_two / "teacher_plan.json")
            integrator = plan["hypotheses"][2]
            enhancer = plan["hypotheses"][3]
            integrator_prompt = (round_two / "prompts" / "student_3.md").read_text(encoding="utf-8")
            enhancer_prompt = (round_two / "prompts" / "student_4.md").read_text(encoding="utf-8")
        self.assertEqual(integrator["role_mode"], "epd_integration")
        self.assertEqual(len(integrator["epd_record_ids"]), 2)
        self.assertEqual(enhancer["role_mode"], "epd_enhancement")
        self.assertEqual(len(enhancer["epd_record_ids"]), 1)
        self.assertIn("## Integrator Operating Protocol", integrator_prompt)
        self.assertIn("implementation_diff_artifact", integrator_prompt)
        self.assertIn("## Enhancer Operating Protocol", enhancer_prompt)
        self.assertIn("implementation_diff_artifact", enhancer_prompt)

    def test_teacher_power_stage_prompt_carries_controller_semantics(self) -> None:
        from goalevolve.agents.teacher import CodexTeacher

        diagnosis = SimpleNamespace(to_dict=lambda: {})
        plan = CodexTeacher._plan_prompt(
            parent=self.parent,
            diagnosis=diagnosis,
            epd={},
            observations={},
            previous_review={},
            fallback=(self.hypothesis,),
            decision_context={"stage": "power_reclaim"},
        )
        self.assertIn('"full_contract_distance_required_for_promotion": false', plan)
        self.assertIn('"formula_for_minimized_metrics": "before - after"', plan)
        self.assertIn("Do not additionally require full three-metric distance", plan)

    def test_teacher_cannot_turn_verified_slot_into_noop(self) -> None:
        executable = Hypothesis(
            "r8_student_1_deep4",
            "deep4",
            "Enable and execute four-move windows in RepairPowerPolicy.",
            ("src/rsz/src/policy/RepairPowerPolicy.cc",),
            ("deep4_examined",),
            ("deep4_card",),
            "deep4:policy",
        )
        raw = {
            "hypotheses": [
                {
                    "student_id": "student_1",
                    "role": "repair",
                    "claim": "Withholding execution: these windows remain suppressed; retain the parent.",
                    "source_hooks": ["src/rsz/src/policy/RepairPowerPolicy.cc"],
                    "expected_signals": ["deep4_examined"],
                }
            ]
        }
        sanitized = CodexTeacher._sanitize_hypotheses(raw["hypotheses"], (executable,))
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0].claim, executable.claim)

    def test_teacher_keeps_one_slot_for_allocation_repair_noop(self) -> None:
        raw = {
            "student_id": "student_1",
            "role": "repair",
            "claim": "Repair experiment allocation: do not run this mechanism until a controller revision reopens it.",
        }
        sanitized = CodexTeacher._sanitize_hypotheses((raw,), (self.hypothesis,))
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0].claim, self.hypothesis.claim)

    def test_teacher_can_select_only_a_controller_verified_explorer_option(self) -> None:
        alternate = Hypothesis(
            "candidate_1_route", "route", "route claim",
            ("src/grt/src/Route.cc",), ("route_examined",), ("route_card",), "route",
            student_id="candidate_1",
        )
        slot = Hypothesis(
            "r2_student_1_timing", "timing", "timing claim",
            ("src/rsz/src/Timing.cc",), ("timing_examined",), ("timing_card",), "timing",
            student_id="student_1",
            candidate_options=(alternate.to_dict(),),
        )
        selected = CodexTeacher._sanitize_hypotheses(
            (
                {
                    "student_id": "student_1",
                    "candidate_id": "route_card",
                    "claim": "Use the controller-verified route mechanism.",
                },
            ),
            (slot,),
        )
        self.assertEqual(selected[0].retrieval_ids, ("route_card",))
        self.assertEqual(selected[0].source_hooks, ("src/grt/src/Route.cc",))
        self.assertEqual(selected[0].student_id, "student_1")
        self.assertEqual(selected[0].student_role, "explorer")

    def test_teacher_duplicate_explorer_selection_keeps_the_second_slot_distinct(self) -> None:
        first = Hypothesis(
            "r2_student_1_timing", "timing", "timing claim",
            ("src/rsz/src/Timing.cc",), ("timing_examined",), ("timing_card",), "timing",
            student_id="student_1",
        )
        second = Hypothesis(
            "r2_student_2_route", "route", "route claim",
            ("src/grt/src/Route.cc",), ("route_examined",), ("route_card",), "route",
            student_id="student_2",
        )
        options = tuple(item.to_dict() for item in (first, second))
        fallback = (
            Hypothesis(**{**first.to_dict(), "candidate_options": options}),
            Hypothesis(**{**second.to_dict(), "candidate_options": options}),
        )
        selected = CodexTeacher._sanitize_hypotheses(
            (
                {"student_id": "student_1", "candidate_id": "timing_card"},
                {"student_id": "student_2", "candidate_id": "timing_card"},
            ),
            fallback,
        )
        self.assertEqual(selected[0].retrieval_ids, ("timing_card",))
        self.assertEqual(selected[1].retrieval_ids, ("route_card",))

    def test_teacher_keeps_an_integrator_epd_pair_atomic(self) -> None:
        slot = Hypothesis(
            "r2_student_3_integrate", "integration", "combine claims",
            ("src/rsz/src/Timing.cc", "src/rsz/src/Power.cc"),
            ("timing_examined", "power_retained"), ("integration",), "integration",
            student_role="integrator",
            role_mode="epd_integration",
            epd_record_ids=("EPD_timing", "EPD_power"),
            student_id="student_3",
        )
        selected = CodexTeacher._sanitize_hypotheses(
            (
                {
                    "student_id": "student_3",
                    "epd_record_ids": ("EPD_power", "EPD_unknown"),
                },
            ),
            (slot,),
        )
        self.assertEqual(selected[0].epd_record_ids, ("EPD_timing", "EPD_power"))

    def test_round_robin_planner_uses_four_explorers_without_epd_evidence(self) -> None:
        cards = tuple(
            MechanismCard(
                f"card_{index}", f"family_{index}", ("tns",),
                (f"src/rsz/src/F{index}.cc",), (f"signal_{index}",), f"claim {index}",
            )
            for index in range(4)
        )
        from goalevolve.planning.retrieval import RoundRobinPlanner

        plans = RoundRobinPlanner(cards).plan(
            contract=self.contract,
            parent=self.parent,
            round_index=1,
            student_ids=("student_1", "student_2", "student_3", "student_4"),
            state_root=Path("/tmp"),
        )
        self.assertEqual([plan.student_role for plan in plans], ["explorer", "explorer", "explorer", "explorer"])
        self.assertEqual(plans[0].student_id, "student_1")

    def test_telemetry_repair_forbids_proxy_activation_counts(self) -> None:
        context = GoalEvolveEngine._telemetry_repair_context(
            candidate=CandidateResult(
                "student_1",
                self.hypothesis,
                {"tns_abs_ns": 100.0, "leakage_power_pw": 200.0},
                {},
                [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                "+change",
                "source",
            ),
            verdict=SimpleNamespace(state="pending"),
            workspace=Path("/tmp/student"),
        )
        self.assertIn("never derive a deep-window signal from an earlier prefix/tranche", context)

    def test_round_token_ledger_aggregates_attempts_without_decision_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "rounds" / "round_001" / "teacher" / "plan"
            second = root / "rounds" / "round_001" / "students" / "student_1" / "artifacts" / "codex"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            atomic_json(first / "token_usage_attempt_01.json", {"identity": "teacher", "operation_id": "r001_teacher_plan", "attempt": 1, "completed": True, "returncode": 0, "usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2, "reasoning_output_tokens": 1}})
            atomic_json(second / "token_usage_attempt_01.json", {"identity": "student_1", "operation_id": "r001_student_1", "attempt": 1, "completed": True, "returncode": 0, "usage": {"input_tokens": 20, "cached_input_tokens": 5, "output_tokens": 3, "reasoning_output_tokens": 2}})
            report = record_round_token_usage(state_root=root, round_root=root / "rounds" / "round_001", round_index=1)
            self.assertEqual(report["decision_role"], "observer_only")
            self.assertEqual(report["call_count"], 2)
            self.assertEqual(report["totals"]["input_tokens"], 30)
            self.assertEqual(report["totals"]["uncached_input_tokens"], 21)
            self.assertEqual(load_json(root / "token_usage.json")["totals"]["total_tokens"], 35)

    def test_diagnosis_prefers_stage_that_regresses_from_best_checkpoint(self) -> None:
        from goalevolve.planning.diagnosis import diagnose

        parent = Parent("baseline", {"tns_abs_ns": 12.72, "leakage_power_pw": 95_400_000.0}, "base", "hash", 0.2)
        diagnosis = diagnose(
            contract=build_contract(
                design="unit",
                baseline_metrics=parent.metrics,
                target_metrics={"tns_abs_ns": 10.176, "leakage_power_pw": 85_860_000.0},
            ),
            parent=parent,
            checkpoints={
                "post_repair_design": {"tns_abs_ns": 9.35},
                "post_route": {"tns_abs_ns": 12.72},
            },
        )
        self.assertEqual(diagnosis.responsible_stage, "post_route")

    def test_diagnosis_uses_physical_checkpoint_order_not_json_key_order(self) -> None:
        from goalevolve.planning.diagnosis import diagnose

        parent = Parent("baseline", {"tns_abs_ns": 104.0, "leakage_power_pw": 110.0}, "base", "hash", 0.2)
        diagnosis = diagnose(
            contract=build_contract(
                design="unit",
                baseline_metrics=parent.metrics,
                target_metrics={"tns_abs_ns": 50.0, "leakage_power_pw": 140.0},
            ),
            parent=parent,
            checkpoints={
                "post_route": {"tns_abs_ns": 104.0, "leakage_power_pw": 110.0},
                "post_repair_power": {"tns_abs_ns": 170.0, "leakage_power_pw": 110.0},
                "pre_repair": {"tns_abs_ns": 45.0, "leakage_power_pw": 155.0},
                "post_repair_design": {"tns_abs_ns": 60.0, "leakage_power_pw": 155.0},
            },
        )
        self.assertEqual(list(diagnosis.checkpoint_effects), ["pre_repair", "post_repair_design", "post_repair_power", "post_route"])
        self.assertEqual(diagnosis.checkpoint_effects["post_repair_power"]["leakage_power_pw"], 45.0)
        self.assertEqual(diagnosis.checkpoint_effects["post_repair_power"]["tns_abs_ns"], -110.0)
        self.assertEqual(diagnosis.checkpoint_effect_semantics["positive"], "stage improved the metric")
        self.assertIn("do not rerun a no-diff baseline", diagnosis.baseline_policy)

    def test_executor_captures_output_after_streamed_wait(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = ResilientCommandRunner(ExecutionPolicy(timeout_s=5, retries=0, min_free_gb=0)).run(
                command=["python3", "-c", "print('executor-ok')"],
                cwd=Path(temporary),
            )
        self.assertTrue(report.ok)
        self.assertIn("executor-ok", report.stdout_tail)

    def test_executor_preserves_complete_log_for_official_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "evaluation.log"
            report = ResilientCommandRunner(ExecutionPolicy(timeout_s=5, retries=0, min_free_gb=0)).run(
                command=["python3", "-c", "print('tns max -7.84'); print('x' * 5000); print('Total 0.1 0.2 0.03 0.23')"],
                cwd=Path(temporary),
                output_log=log,
            )
            text = log.read_text(encoding="utf-8")
        self.assertTrue(report.ok)
        self.assertLessEqual(len(report.stdout_tail), 4000)
        self.assertIn("tns max -7.84", text)
        self.assertIn("Total 0.1 0.2 0.03 0.23", text)

    def test_executor_writes_output_log_while_command_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "live.log"
            result: list[object] = []

            def run() -> None:
                result.append(
                    ResilientCommandRunner(ExecutionPolicy(timeout_s=5, retries=0, min_free_gb=0)).run(
                        command=["python3", "-c", "import time; print('first', flush=True); time.sleep(1); print('last', flush=True)"],
                        cwd=root,
                        output_log=log,
                    )
                )

            worker = threading.Thread(target=run)
            worker.start()
            for _ in range(20):
                if log.is_file() and "first" in log.read_text(encoding="utf-8"):
                    break
                time.sleep(0.05)
            self.assertTrue(log.is_file())
            self.assertIn("first", log.read_text(encoding="utf-8"))
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(result[0].ok)

    def test_four_of_four_gate_and_mechanism_attribution(self) -> None:
        checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
        valid = CandidateResult("student_1", self.hypothesis, {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0}, {"accepted": 1.0}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+change\n", "commit")
        verdict = classify_candidate(contract=self.contract, parent=self.parent, candidate=valid)
        self.assertEqual(verdict.state, "validated")
        no_lec = CandidateResult("student_2", self.hypothesis, valid.metrics, valid.phase_signals, checks[:-1], valid.implementation_diff, "commit")
        self.assertEqual(classify_candidate(contract=self.contract, parent=self.parent, candidate=no_lec).state, "invalid")
        no_signal = CandidateResult("student_3", self.hypothesis, valid.metrics, {}, checks, valid.implementation_diff, "commit")
        self.assertEqual(
            classify_candidate(contract=self.contract, parent=self.parent, candidate=no_signal).state,
            "verified_qor_unattributed",
        )

    def test_signed_mechanism_signal_is_attribution_not_absence(self) -> None:
        checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
        signed = Hypothesis(
            "signed",
            "power",
            "signed leakage telemetry",
            ("src/rsz/src/RecoverPower.cc",),
            ("leakage_delta",),
            ("card",),
            "signed",
        )
        candidate = CandidateResult(
            "student",
            signed,
            {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0},
            {"leakage_delta": -0.5},
            checks,
            "+++ b/src/rsz/src/RecoverPower.cc\n+change\n",
            "commit",
        )
        self.assertTrue(classify_candidate(contract=self.contract, parent=self.parent, candidate=candidate).mechanism_fired)

    def test_activation_signals_do_not_require_zero_valued_outcome_counter(self) -> None:
        checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
        hypothesis = Hypothesis(
            "freshness",
            "mt1_freshness",
            "claim",
            ("src/rsz/src/policy/SetupMt1Policy.cc",),
            ("reestimated", "choice_changed", "retained"),
            ("freshness",),
            "freshness",
            activation_signals=("reestimated", "retained"),
        )
        candidate = CandidateResult(
            "student",
            hypothesis,
            {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0},
            {"reestimated": 12.0, "choice_changed": 0.0, "retained": 1.0},
            checks,
            "+++ b/src/rsz/src/policy/SetupMt1Policy.cc\n+change\n",
            "commit",
        )
        verdict = classify_candidate(contract=self.contract, parent=self.parent, candidate=candidate)
        self.assertTrue(verdict.mechanism_fired)
        self.assertEqual(verdict.state, "validated")

    def test_sigsegv_is_returned_to_same_student_for_repair(self) -> None:
        candidate = CandidateResult(
            "student", self.hypothesis, {}, {}, [], "", "",
            evaluation_error="flow_or_metrics_failed:terminated_signal:11",
        )
        self.assertTrue(GoalEvolveEngine._repairable_engineering_failure(candidate))

    def test_electrical_constraint_context_preserves_rmp_assignment(self) -> None:
        hypothesis = Hypothesis(
            "rmp_electrical_guard",
            "rmp_area_electrical_guard",
            "Restore area-mode ABC candidates that introduce electrical violations.",
            ("src/rmp/src/Restructure.cpp",),
            ("rmp_area_electrical_rejects",),
            ("teacher_idea:idea_1",),
            "rmp_area_electrical_guard",
            evaluation_mode="power_only",
            timing_recipe_id="rmp_area_power",
        )
        candidate = CandidateResult(
            "student_1",
            hypothesis,
            {"drv_count": 160.0},
            {},
            [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
            "+++ b/src/rmp/src/Restructure.cpp\n+guard\n",
            "candidate",
        )

        context = GoalEvolveEngine._constraint_failure_context(
            candidate=candidate,
            workspace=Path("/tmp/rmp-workspace"),
        )

        self.assertIn("mechanism_family: rmp_area_electrical_guard", context)
        self.assertIn("source_hooks: src/rmp/src/Restructure.cpp", context)
        self.assertIn("evaluation_recipe: rmp_area_power", context)
        self.assertIn("Restore area-mode ABC candidates", context)
        self.assertNotIn("repair_power mechanism", context)

    def test_constraint_repair_skips_parent_inherited_drv(self) -> None:
        candidate = CandidateResult(
            "student_1",
            self.hypothesis,
            {"drv_count": 160.0},
            {},
            [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
            "+++ b/src/rsz/src/RecoverPower.cc\n+change\n",
            "candidate",
        )
        parent = Parent(
            "baseline",
            {"drv_count": 160.0},
            "base",
            "hash",
            1.0,
        )

        self.assertFalse(
            GoalEvolveEngine._repairable_constraint_failure(
                candidate=candidate,
                parent=parent,
            )
        )

    def test_constraint_repair_handles_new_drv_regression(self) -> None:
        candidate = CandidateResult(
            "student_1",
            self.hypothesis,
            {"drv_count": 160.0},
            {},
            [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
            "+++ b/src/rsz/src/RecoverPower.cc\n+change\n",
            "candidate",
        )
        parent = Parent(
            "baseline",
            {"drv_count": 0.0},
            "base",
            "hash",
            1.0,
        )

        self.assertTrue(
            GoalEvolveEngine._repairable_constraint_failure(
                candidate=candidate,
                parent=parent,
            )
        )

    def test_stage_patch_scope_rejects_mixed_power_and_timing_edits(self) -> None:
        mixed = CandidateResult(
            "student",
            Hypothesis(
                "power",
                "repair_power",
                "claim",
                ("src/rsz/src/policy/RepairPowerPolicy.cc",),
                ("accepted",),
                ("card",),
                "power",
                allowed_patch_paths=("src/rsz/src/policy/RepairPowerPolicy.cc",),
            ),
            {},
            {},
            [],
            "+++ b/src/rsz/src/policy/RepairPowerPolicy.cc\n+change\n"
            "+++ b/src/rsz/src/policy/SetupCritVtSwapPolicy.cc\n+change\n",
            "commit",
        )
        report = preflight_candidate(mixed, allowed_patch_roots=("src/rsz",), allowed_patch_paths=mixed.hypothesis.allowed_patch_paths)
        self.assertFalse(report.ok)
        self.assertIn("outside_assigned_patch_scope:src/rsz/src/policy/SetupCritVtSwapPolicy.cc", report.violations)

    def test_preflight_accepts_a_header_only_cpp_patch(self) -> None:
        candidate = CandidateResult(
            "student",
            self.hypothesis,
            {},
            {},
            [],
            "+++ b/src/rsz/include/rsz/RecoverPower.h\n+// documented boundary\n",
            "commit",
        )
        report = preflight_candidate(candidate, allowed_patch_roots=("src/rsz",))
        self.assertTrue(report.ok)

    def test_timing_recovery_prefers_direct_repair_timing_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "knowledge").mkdir()
            atomic_json(root / "knowledge" / "feedback.json", {})
            atomic_json(root / "knowledge" / "retrieval_ledger.json", {"rounds": []})
            selected = DiverseRetriever().retrieve(
                parent=self.parent,
                symptoms=("tns", "timing", "timing_recovery"),
                state_root=root,
                count=2,
            )
        self.assertEqual(
            [card.card_id for card in selected],
            [
                "timing_recovery_rmp_path_cone_v4",
                "timing_recovery_rmp_path_cone_halo_v5",
            ],
        )

    def test_crit_vt_card_uses_its_own_recipe_not_student_index_fallback(self) -> None:
        cards = (
            MechanismCard(
                "crit_vt_probe",
                "timing_recovery_crit_vt_probe",
                ("tns", "timing_recovery", "crit_vt"),
                ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc",),
                ("examined",),
                "crit-vt probe",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            plan = DiversePlanner(DiverseRetriever(cards)).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=1,
                student_ids=("student_4",),
                state_root=Path(temporary),
                decision_context={"stage": "timing_recovery", "evaluation_mode": "power_then_timing"},
            )
        self.assertEqual(plan[0].timing_recipe_id, "crit_vt_deep")

    def test_timing_schedule_memory_persists_outcome_and_cell_tradeoff_rollup(self) -> None:
        from goalevolve.planning.timing_recovery import record_schedule_memory, schedule_memory_summary

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record_schedule_memory(
                root,
                [{
                    "round": 1,
                    "student_id": "student_1",
                    "recipe_id": "mt1_deep",
                    "metrics": {"tns_abs_ns": 10.0},
                    "verdict": {"state": "validated", "mechanism_fired": True, "goal_distance": 0.1},
                    "cell_tradeoff": {
                        "overlap_rate_of_power_replacements": 0.5,
                        "exact_reversion_rate_of_power_replacements": 0.1,
                        "examples": [{"instance": "do_not_copy_instance_lists_into_prompt"}],
                    },
                }],
            )
            summary = schedule_memory_summary(root)
            persisted = load_json(root / "knowledge" / "timing_schedule_summary.json")
        self.assertEqual(summary["recommended_recipe_ids"], ["mt1_deep"])
        self.assertEqual(summary["recipe_rollup"]["mt1_deep"]["activated_samples"], 1)
        self.assertEqual(summary["recipe_rollup"]["mt1_deep"]["mean_power_overlap_rate"], 0.5)
        self.assertNotIn("examples", summary["recent"][0]["cell_tradeoff"])
        self.assertEqual(persisted, summary)

    def test_timing_recovery_remeasures_parent_in_candidate_flow_mode(self) -> None:
        class StageEvaluator:
            name = "stage_evaluator"

            def __init__(self) -> None:
                self.calls = 0

            def evaluate_parent(self, **kwargs):
                self.calls += 1
                self.mode = kwargs["optimization_mode"]
                return {
                    "ok": True,
                    "metrics": {"tns_abs_ns": 40.0, "leakage_power_pw": 160.0},
                    "checks": [],
                    "artifacts": {},
                }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = Parent("low_power", {"tns_abs_ns": 70.0, "leakage_power_pw": 160.0}, "commit", "hash", 0.0, "power_only")
            (root / "parents" / parent.source_hash / "source").mkdir(parents=True)
            evaluator = StageEvaluator()
            engine = GoalEvolveEngine(
                contract=self.contract,
                state_root=root,
                planner=SimpleNamespace(name="planner"),
                evaluator=evaluator,
                workspace_provider=SimpleNamespace(name="workspace"),
                promotion_policy=SimpleNamespace(name="promotion"),
            )
            staged = engine._stage_matched_parent(
                parent=parent,
                decision_context={"evaluation_mode": "power_then_timing"},
            )
            repeated = engine._stage_matched_parent(
                parent=staged,
                decision_context={"evaluation_mode": "power_then_timing"},
            )
        self.assertEqual(evaluator.calls, 1)
        self.assertEqual(evaluator.mode, "power_then_timing")
        self.assertEqual(staged.evaluation_mode, "power_then_timing")
        self.assertEqual(staged.metrics["tns_abs_ns"], 40.0)
        self.assertEqual(repeated, staged)

    def test_power_stage_remeasures_imported_full_flow_parent(self) -> None:
        class StageEvaluator:
            name = "stage_evaluator"

            def __init__(self) -> None:
                self.calls = 0

            def evaluate_parent(self, **kwargs):
                self.calls += 1
                self.mode = kwargs["optimization_mode"]
                return {
                    "ok": True,
                    "metrics": {"tns_abs_ns": 170.0, "leakage_power_pw": 110.0},
                    "checks": [],
                    "artifacts": {},
                }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = Parent("imported_full_flow", {"tns_abs_ns": 51.0, "leakage_power_pw": 120.0}, "commit", "hash", 0.0, "power_then_timing")
            (root / "parents" / parent.source_hash / "source").mkdir(parents=True)
            evaluator = StageEvaluator()
            engine = GoalEvolveEngine(
                contract=self.contract,
                state_root=root,
                planner=SimpleNamespace(name="planner"),
                evaluator=evaluator,
                workspace_provider=SimpleNamespace(name="workspace"),
                promotion_policy=SimpleNamespace(name="promotion"),
            )
            staged = engine._stage_matched_parent(
                parent=parent,
                decision_context={"evaluation_mode": "power_only"},
            )
            repeated = engine._stage_matched_parent(
                parent=staged,
                decision_context={"evaluation_mode": "power_only"},
            )
        self.assertEqual(evaluator.calls, 1)
        self.assertEqual(evaluator.mode, "power_only")
        self.assertEqual(staged.evaluation_mode, "power_only")
        self.assertEqual(staged.metrics["leakage_power_pw"], 110.0)
        self.assertEqual(repeated, staged)

    def test_stage_parent_does_not_remeasure_same_mode_after_evaluator_revision(self) -> None:
        class StageEvaluator:
            name = "stage_evaluator"

            def __init__(self) -> None:
                self.calls = 0
                self.revision = "v1"

            def baseline_identity(self, **kwargs):
                return {"revision": self.revision, **kwargs}

            def evaluate_parent(self, **_: object):
                self.calls += 1
                return {
                    "ok": True,
                    "metrics": {"tns_abs_ns": 100.0 - self.calls, "leakage_power_pw": 110.0},
                    "checks": [],
                    "artifacts": {},
                }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = Parent("same_mode", {"tns_abs_ns": 100.0, "leakage_power_pw": 110.0}, "commit", "hash", 0.0, "power_only")
            (root / "parents" / parent.source_hash / "source").mkdir(parents=True)
            evaluator = StageEvaluator()
            engine = GoalEvolveEngine(
                contract=self.contract,
                state_root=root,
                planner=SimpleNamespace(name="planner"),
                evaluator=evaluator,
                workspace_provider=SimpleNamespace(name="workspace"),
                promotion_policy=SimpleNamespace(name="promotion"),
            )
            staged_v1 = engine._stage_matched_parent(parent=parent, decision_context={"evaluation_mode": "power_only"})
            evaluator.revision = "v2"
            staged_v2 = engine._stage_matched_parent(parent=staged_v1, decision_context={"evaluation_mode": "power_only"})
        self.assertEqual(evaluator.calls, 0)
        self.assertEqual(staged_v1, parent)
        self.assertEqual(staged_v2, parent)

    def test_recipe_baseline_cache_includes_evaluator_schedule_identity(self) -> None:
        class RecipeEvaluator:
            name = "recipe_evaluator"

            def __init__(self) -> None:
                self.calls = 0
                self.revision = "single_liberty"

            def baseline_identity(self, **kwargs):
                return {"revision": self.revision, **kwargs}

            def evaluate_parent(self, **kwargs):
                self.calls += 1
                return {
                    "ok": True,
                    "metrics": {"tns_abs_ns": 40.0, "leakage_power_pw": 160.0},
                    "checks": [],
                    "artifacts": {},
                }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = Parent("low_power", {"tns_abs_ns": 70.0, "leakage_power_pw": 160.0}, "commit", "hash", 0.0, "power_then_timing")
            (root / "parents" / parent.source_hash / "source").mkdir(parents=True)
            evaluator = RecipeEvaluator()
            engine = GoalEvolveEngine(
                contract=self.contract,
                state_root=root,
                planner=SimpleNamespace(name="planner"),
                evaluator=evaluator,
                workspace_provider=SimpleNamespace(name="workspace"),
                promotion_policy=SimpleNamespace(name="promotion"),
            )
            hypothesis = SimpleNamespace(evaluation_mode="power_then_timing", timing_recipe_id="legacy_mt")
            context = {"stage": "timing_recovery"}
            engine._recipe_matched_parent(parent=parent, hypothesis=hypothesis, decision_context=context)
            engine._recipe_matched_parent(parent=parent, hypothesis=hypothesis, decision_context=context)
            evaluator.revision = "all_standard_cell_liberties"
            engine._recipe_matched_parent(parent=parent, hypothesis=hypothesis, decision_context=context)
        self.assertEqual(evaluator.calls, 2)

    def test_recipe_baseline_is_measured_for_a_degraded_complete_timing_candidate(self) -> None:
        """A failed source experiment still needs recipe-level attribution."""

        class OneTimingPlanner:
            name = "one_timing_planner"

            def plan(self, **_: object) -> list[Hypothesis]:
                return [
                    Hypothesis(
                        "r001_student_1_mt1",
                        "mt1_guard",
                        "Measure a bounded MT1 admission guard.",
                        ("src/rsz/src/policy/SetupMt1Policy.cc",),
                        ("mt1_guard_examined",),
                        ("teacher_idea:idea_1",),
                        "mt1_guard",
                        evaluation_mode="power_then_timing",
                        timing_recipe_id="mt1_deep",
                        student_id="student_1",
                    )
                ]

        class TimingEvaluator:
            name = "timing_evaluator"
            config = SimpleNamespace(allowed_patch_roots=("src/rsz",))

            def __init__(self) -> None:
                self.parent_recipe_calls: list[str] = []

            def evaluate_parent(self, *, parent, timing_recipe_id: str = "legacy_setup", **_: object):
                self.parent_recipe_calls.append(timing_recipe_id)
                return {
                    "ok": True,
                    "metrics": dict(parent.metrics),
                    "checks": [],
                    "artifacts": {},
                }

            def evaluate(self, *, parent, hypothesis, student_id, **_: object) -> CandidateResult:
                metrics = dict(parent.metrics)
                metrics["tns_abs_ns"] = 15.0
                return CandidateResult(
                    student_id,
                    hypothesis,
                    metrics,
                    {"mt1_guard_examined": 1.0},
                    [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                    "+++ b/src/rsz/src/policy/SetupMt1Policy.cc\n+// bounded guard\n",
                    "degraded-mt1-source",
                )

        contract = build_contract(
            design="recipe_baseline_attribution",
            baseline_metrics={
                "tns_abs_ns": 10.0,
                "dynamic_power_pw": 200.0,
                "leakage_power_pw": 100.0,
            },
            target_metrics={
                "tns_abs_ns": 5.0,
                "dynamic_power_pw": 300.0,
                "leakage_power_pw": 150.0,
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "seed"
            hook = seed / "src/rsz/src/policy/SetupMt1Policy.cc"
            hook.parent.mkdir(parents=True)
            hook.write_text("void SetupMt1Policy::apply() {}\n", encoding="utf-8")
            evaluator = TimingEvaluator()
            engine = GoalEvolveEngine(
                contract,
                root / "campaign",
                OneTimingPlanner(),
                evaluator,
                IsolatedWorkspace(seed),
                PowerFirstPromotion(),
                student_ids=("student_1",),
            )
            initial = engine.initialize(baseline_metrics=dict(contract.baseline_metrics))
            engine.run_round(
                round_index=1,
                parent=replace(initial, evaluation_mode="power_then_timing"),
            )
            baselines = list((root / "campaign" / "stage_baselines").glob("*_mt1_deep_*/baseline.json"))
        self.assertEqual(evaluator.parent_recipe_calls, ["mt1_deep"])
        self.assertEqual(len(baselines), 1)

    def test_failed_recipe_baseline_is_cached_and_falls_back_to_lineage(self) -> None:
        class FailingRecipeEvaluator:
            name = "failing_recipe_evaluator"

            def __init__(self) -> None:
                self.calls = 0

            def baseline_identity(self, **kwargs):
                return {"revision": "unsafe_recipe", **kwargs}

            def evaluate_parent(self, **kwargs):
                self.calls += 1
                return {
                    "ok": False,
                    "error": "openroad_signal_11",
                    "metrics": {},
                    "checks": [],
                    "artifacts": {},
                }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = Parent(
                "low_power",
                {"tns_abs_ns": 70.0, "leakage_power_pw": 160.0},
                "commit",
                "hash",
                0.0,
                "power_then_timing",
            )
            (root / "parents" / parent.source_hash / "source").mkdir(parents=True)
            evaluator = FailingRecipeEvaluator()
            engine = GoalEvolveEngine(
                contract=self.contract,
                state_root=root,
                planner=SimpleNamespace(name="planner"),
                evaluator=evaluator,
                workspace_provider=SimpleNamespace(name="workspace"),
                promotion_policy=SimpleNamespace(name="promotion"),
            )
            hypothesis = SimpleNamespace(
                evaluation_mode="power_then_timing",
                timing_recipe_id="measured_critical_path_deep",
            )
            context = {"stage": "adaptive_tradeoff"}
            first_parent, first_status = engine._recipe_matched_parent(
                parent=parent,
                hypothesis=hypothesis,
                decision_context=context,
            )
            second_parent, second_status = engine._recipe_matched_parent(
                parent=parent,
                hypothesis=hypothesis,
                decision_context=context,
            )
        self.assertEqual(evaluator.calls, 1)
        self.assertEqual(first_parent, parent)
        self.assertEqual(second_parent, parent)
        self.assertTrue(first_status.startswith("unavailable:"))
        self.assertEqual(first_status, second_status)

    def test_power_first_promotes_power_gain_with_bounded_timing_degradation(self) -> None:
        contract = build_contract(
            design="power_first",
            baseline_metrics={"tns_abs_ns": 8.89, "dynamic_power_pw": 381_931_800_000.0, "leakage_power_pw": 68_200_000.0},
            target_metrics={"tns_abs_ns": 12.0, "dynamic_power_pw": 350_000_000_000.0, "leakage_power_pw": 35_000_000.0},
        )
        parent = Parent(
            "power_parent",
            {"tns_abs_ns": 8.87, "dynamic_power_pw": 379_937_000_000.0, "leakage_power_pw": 63_000_000.0, "drv_count": 0.0},
            "base",
            "hash",
            0.16,
        )
        candidate = CandidateResult(
            "student_1",
            self.hypothesis,
            {"tns_abs_ns": 20.0, "dynamic_power_pw": 370_000_000_000.0, "leakage_power_pw": 50_000_000.0, "drv_count": 0.0},
            {"accepted": 1.0},
            [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
            "+++ b/src/rsz/src/RecoverPower.cc\n+change\n",
            "commit",
        )
        policy = PowerFirstPromotion(power_stage_tns_ceiling_ns=30.0)
        verdict = policy.classify(contract=contract, parent=parent, candidate=candidate)
        self.assertEqual(policy.context(contract=contract, parent=parent)["stage"], "power_reclaim")
        self.assertFalse(policy.context(contract=contract, parent=parent)["full_contract_distance_required_for_promotion"])
        self.assertEqual(verdict.state, "validated")
        self.assertIn("power_stage_key", candidate.artifacts)

        unsafe = CandidateResult(
            "student_2",
            self.hypothesis,
            {**candidate.metrics, "tns_abs_ns": 30.01},
            {"accepted": 1.0},
            candidate.checks,
            candidate.implementation_diff,
            "commit-unsafe",
        )
        self.assertEqual(policy.classify(contract=contract, parent=parent, candidate=unsafe).state, "refuted")

    def test_power_first_selects_lexicographic_power_frontier_not_residual_sum(self) -> None:
        contract = build_contract(
            design="power_frontier",
            baseline_metrics={"tns_abs_ns": 8.89, "dynamic_power_pw": 381_931_800_000.0, "leakage_power_pw": 68_200_000.0},
            target_metrics={"tns_abs_ns": 12.0, "dynamic_power_pw": 350_000_000_000.0, "leakage_power_pw": 35_000_000.0},
        )
        parent = Parent("parent", {"tns_abs_ns": 8.87, "dynamic_power_pw": 379_937_000_000.0, "leakage_power_pw": 63_000_000.0, "drv_count": 0.0}, "base", "hash", 0.16)
        checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
        # Candidate A lowers the worst unresolved residual more, even though
        # its residual sum is larger because it spends dynamic-power headroom.
        first = CandidateResult("student_a", self.hypothesis, {"tns_abs_ns": 20.0, "dynamic_power_pw": 422_000_000_000.0, "leakage_power_pw": 48_000_000.0, "drv_count": 0.0}, {"accepted": 1.0}, checks, "+++ a\n", "a")
        second = CandidateResult("student_b", self.hypothesis, {"tns_abs_ns": 20.0, "dynamic_power_pw": 350_000_000_000.0, "leakage_power_pw": 48_600_000.0, "drv_count": 0.0}, {"accepted": 1.0}, checks, "+++ b\n", "b")
        policy = PowerFirstPromotion()
        first_verdict = policy.classify(contract=contract, parent=parent, candidate=first)
        second_verdict = policy.classify(contract=contract, parent=parent, candidate=second)
        self.assertGreater(first_verdict.goal_distance, second_verdict.goal_distance)
        chosen = policy.choose(((first, first_verdict), (second, second_verdict)))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen[0].student_id, "student_a")

    def test_power_first_switches_to_final_contract_after_both_power_targets(self) -> None:
        contract = build_contract(
            design="timing_recovery",
            baseline_metrics={"tns_abs_ns": 8.89, "dynamic_power_pw": 381_931_800_000.0, "leakage_power_pw": 68_200_000.0},
            target_metrics={"tns_abs_ns": 12.0, "dynamic_power_pw": 350_000_000_000.0, "leakage_power_pw": 35_000_000.0},
        )
        parent = Parent("low_power", {"tns_abs_ns": 20.0, "dynamic_power_pw": 349_000_000_000.0, "leakage_power_pw": 34_000_000.0, "drv_count": 0.0}, "base", "hash", 0.0)
        candidate = CandidateResult("student", self.hypothesis, {"tns_abs_ns": 10.0, "dynamic_power_pw": 349_000_000_000.0, "leakage_power_pw": 34_000_000.0, "drv_count": 0.0}, {"accepted": 1.0}, [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")], "+++ recovery\n", "commit")
        policy = PowerFirstPromotion()
        self.assertEqual(policy.context(contract=contract, parent=parent)["stage"], "timing_recovery")
        self.assertEqual(policy.classify(contract=contract, parent=parent, candidate=candidate).state, "validated")

    def test_power_protection_expires_into_adaptive_tradeoff_after_round_ten(self) -> None:
        contract = build_contract(
            design="adaptive",
            baseline_metrics={
                "tns_abs_ns": 48.77,
                "dynamic_power_pw": 293_826_000_000.0,
                "leakage_power_pw": 174_000_000.0,
            },
            target_metrics={
                "tns_abs_ns": 53.0,
                "dynamic_power_pw": 250_000_000_000.0,
                "leakage_power_pw": 80_000_000.0,
            },
        )
        parent = Parent(
            "jpeg",
            {
                "tns_abs_ns": 105.31,
                "dynamic_power_pw": 242_893_000_000.0,
                "leakage_power_pw": 107_000_000.0,
            },
            "source",
            "hash",
            0.4,
            "power_only",
        )
        policy = PowerFirstPromotion(power_stage_tns_ceiling_ns=200.0, protected_power_rounds=10)
        self.assertEqual(policy.context(contract=contract, parent=parent, round_index=10)["stage"], "power_reclaim")
        adaptive = policy.context(contract=contract, parent=parent, round_index=11)
        self.assertEqual(adaptive["stage"], "adaptive_tradeoff")
        self.assertEqual(adaptive["dominant_metric"], "tns_abs_ns")
        self.assertEqual(adaptive["candidate_pool_coverage"]["upstream_power_candidates"], 1)

    def test_adaptive_teacher_prompt_does_not_assume_four_fixed_roles(self) -> None:
        from goalevolve.agents.teacher import CodexTeacher
        from goalevolve.planning.diagnosis import diagnose

        prompt = CodexTeacher._plan_prompt(
            parent=self.parent,
            diagnosis=diagnose(contract=self.contract, parent=self.parent, checkpoints={}),
            epd={},
            observations={},
            previous_review={},
            fallback=(),
            contract=self.contract,
            decision_context={"stage": "adaptive_tradeoff"},
        )

        self.assertIn("Controller-provided role envelopes", prompt)
        self.assertNotIn("does not change the four Student roles", prompt)

    def test_adaptive_tradeoff_preserves_satisfied_power_target(self) -> None:
        contract = build_contract(
            design="adaptive",
            baseline_metrics={"tns_abs_ns": 100.0, "dynamic_power_pw": 300.0, "leakage_power_pw": 180.0},
            target_metrics={"tns_abs_ns": 50.0, "dynamic_power_pw": 250.0, "leakage_power_pw": 80.0},
        )
        parent_metrics = {"tns_abs_ns": 105.0, "dynamic_power_pw": 240.0, "leakage_power_pw": 107.0, "drv_count": 0.0}
        parent = Parent("adaptive", parent_metrics, "source", "hash", contract.evaluate(parent_metrics)[0], "power_then_timing")
        hypothesis = Hypothesis(
            "adaptive_h",
            "timing",
            "repair dominant timing",
            ("src/rsz/src/policy/SetupTnsPolicy.cc",),
            ("accepted",),
            ("card",),
            "timing:setup",
            evaluation_mode="power_then_timing",
        )
        checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
        candidate = CandidateResult(
            "student_1",
            hypothesis,
            {"tns_abs_ns": 60.0, "dynamic_power_pw": 255.0, "leakage_power_pw": 100.0, "drv_count": 0.0},
            {"accepted": 1.0},
            checks,
            "+timing change",
            "candidate",
        )
        verdict = PowerFirstPromotion(power_stage_tns_ceiling_ns=200.0).classify(
            contract=contract, parent=parent, candidate=candidate
        )
        self.assertEqual(verdict.state, "refuted")
        self.assertIn("adaptive_regressed_satisfied_target:dynamic_power_pw", verdict.reasons)

    def test_adaptive_planner_preserves_dominant_power_and_handoff_buckets(self) -> None:
        timing_one = MechanismCard(
            "timing_one", "timing_one", ("tns", "timing", "repair_timing_direct"),
            ("src/rsz/src/TimingOne.cc",), ("timing_one",), "timing one",
        )
        timing_two = MechanismCard(
            "timing_two", "timing_two", ("tns", "timing", "repair_timing_direct"),
            ("src/rsz/src/TimingTwo.cc",), ("timing_two",), "timing two",
        )
        upstream_power = MechanismCard(
            "upstream_power", "upstream_power", ("leakage", "dynamic", "power", "power_reclaim"),
            ("src/rsz/src/policy/RepairPowerPolicy.cc",), ("power",), "upstream power",
            power_command_eligible=True,
        )
        handoff = MechanismCard(
            "handoff", "handoff", ("tns", "timing", "timing_power_rebuild", "cell_reversion"),
            ("src/rsz/src/Handoff.cc",), ("handoff",), "handoff",
        )
        with tempfile.TemporaryDirectory() as temporary:
            plans = DiversePlanner(DiverseRetriever((timing_one, timing_two, upstream_power, handoff))).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=11,
                student_ids=("student_1", "student_2", "student_3", "student_4"),
                state_root=Path(temporary),
                decision_context={"stage": "adaptive_tradeoff", "dominant_metric": "tns_abs_ns"},
            )
        selected = [plan.retrieval_ids[0] for plan in plans]
        self.assertEqual({"timing_one", "timing_two"}, set(selected[:2]))
        self.assertEqual(["upstream_power", "handoff"], selected[2:])

    def test_adaptive_planner_keeps_batch_sparse_when_power_bucket_is_exhausted(self) -> None:
        cards = (
            MechanismCard(
                "timing_one", "timing_one", ("tns", "timing", "repair_timing_direct"),
                ("src/rsz/src/TimingOne.cc",), ("timing_one",), "timing one",
            ),
            MechanismCard(
                "timing_two", "timing_two", ("tns", "timing", "repair_timing_direct"),
                ("src/rsz/src/TimingTwo.cc",), ("timing_two",), "timing two",
            ),
            MechanismCard(
                "extra_timing", "extra_timing", ("tns", "timing", "repair_timing_direct"),
                ("src/rsz/src/ExtraTiming.cc",), ("extra_timing",), "extra timing",
            ),
            MechanismCard(
                "handoff", "handoff", ("tns", "timing", "timing_power_rebuild", "cell_reversion"),
                ("src/rsz/src/Handoff.cc",), ("handoff",), "handoff",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            plans = DiversePlanner(DiverseRetriever(cards)).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=11,
                student_ids=("student_1", "student_2", "student_3", "student_4"),
                state_root=Path(temporary),
                decision_context={"stage": "adaptive_tradeoff", "dominant_metric": "tns_abs_ns"},
            )
        self.assertEqual(3, len(plans))
        selected = {plan.retrieval_ids[0] for plan in plans}
        self.assertIn("handoff", selected)
        self.assertEqual(2, len(selected - {"handoff"}))

    def test_adaptive_planning_uses_same_source_recipe_frontier_without_replacing_parent(self) -> None:
        contract = build_contract(
            design="adaptive_frontier",
            baseline_metrics={
                "tns_abs_ns": 48.77,
                "dynamic_power_pw": 293_826_000_000.0,
                "leakage_power_pw": 174_000_000.0,
            },
            target_metrics={
                "tns_abs_ns": 53.0,
                "dynamic_power_pw": 250_000_000_000.0,
                "leakage_power_pw": 80_000_000.0,
            },
        )
        parent_metrics = {
            "tns_abs_ns": 100.33,
            "dynamic_power_pw": 242_892_000_000.0,
            "leakage_power_pw": 108_000_000.0,
            "drv_count": 0.0,
        }
        parent = Parent(
            "round_005:student_1",
            parent_metrics,
            "commit",
            "same-source",
            contract.evaluate(parent_metrics)[0],
            "power_then_timing",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(
                root / "knowledge" / "timing_schedule_memory.json",
                {
                    "records": [
                        {
                            "round": 12,
                            "recipe_id": "mt1_deep",
                            "comparison_parent": {
                                "source_hash": "same-source",
                                "evaluation_mode": "power_then_timing",
                                "metrics": {
                                    "tns_abs_ns": 50.94,
                                    "dynamic_power_pw": 263_883_000_000.0,
                                    "leakage_power_pw": 117_000_000.0,
                                    "drv_count": 0.0,
                                    "runtime_s": 999.0,
                                },
                            },
                        },
                        {
                            "round": 12,
                            "recipe_id": "wrong_lineage",
                            "comparison_parent": {
                                "source_hash": "different-source",
                                "evaluation_mode": "power_then_timing",
                                "metrics": {
                                    "tns_abs_ns": 1.0,
                                    "dynamic_power_pw": 1.0,
                                    "leakage_power_pw": 1.0,
                                    "drv_count": 0.0,
                                },
                            },
                        },
                    ]
                },
            )
            engine = GoalEvolveEngine(
                contract,
                root,
                DiversePlanner(),
                MockEvaluator(),
                IsolatedWorkspace(),
                PowerFirstPromotion(protected_power_rounds=10),
            )
            base = engine.promotion_policy.context(contract=contract, parent=parent, round_index=13)
            self.assertEqual(base["dominant_metric"], "tns_abs_ns")
            updated = engine._adaptive_frontier_context(parent=parent, decision_context=base)
        self.assertEqual(updated["dominant_metric"], "leakage_power_pw")
        self.assertEqual(updated["diagnostic_frontier"]["recipe_id"], "mt1_deep")
        self.assertFalse(updated["diagnostic_frontier"]["promotion_authority"])
        self.assertEqual(updated["diagnostic_frontier"]["metrics"]["tns_abs_ns"], 50.94)
        self.assertNotIn("runtime_s", updated["diagnostic_frontier"]["metrics"])
        self.assertEqual(parent.parent_id, "round_005:student_1")

    def test_power_only_flow_persists_full_cell_replacement_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "post_repair_design.v").write_text(
                "module top;\nCELL_A u1 ();\nCELL_B u2 ();\nendmodule\n",
                encoding="utf-8",
            )
            (root / "post_repair_power.v").write_text(
                "module top;\nCELL_L u1 ();\nCELL_B u2 ();\nendmodule\n",
                encoding="utf-8",
            )
            tradeoff = _power_timing_cell_tradeoff(root)
        self.assertTrue(tradeoff["available"])
        self.assertTrue(tradeoff["power_reclaim_available"])
        self.assertFalse(tradeoff["timing_interaction_available"])
        self.assertEqual(tradeoff["power_reclaim_replacements"], 1)
        self.assertEqual(tradeoff["power_replacements"][0]["instance"], "u1")

    def test_multiround_common_parent_feedback_and_diversity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            engine = GoalEvolveEngine(self.contract, root, DiversePlanner(), MockEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion())
            final_parent = engine.run(rounds=3)
            self.assertLess(final_parent.goal_distance, self.parent.goal_distance)
            previous_parent = "baseline"
            expected_student_counts = (4, 3, 3)
            for index, expected_count in enumerate(expected_student_counts, start=1):
                round_data = load_json(root / "rounds" / f"round_{index:03d}" / "round.json")
                self.assertEqual(round_data["common_parent_id_at_start"], previous_parent)
                self.assertEqual(round_data["student_count"], expected_count)
                self.assertEqual(round_data["promoted_student"], "student_1")
                self.assertEqual(len({row["hypothesis_id"] for row in round_data["results"]}), expected_count)
                previous_parent = f"round_{index:03d}:student_1"
            ledger = load_json(root / "knowledge" / "retrieval_ledger.json")
            self.assertEqual(len(ledger["rounds"]), 3)
            # The ledger records only fresh retrieval cards. EPD integrator /
            # enhancer tasks are derived from persisted verified mechanisms,
            # not falsely replayed as new cards.
            self.assertTrue(all(0 < len(row["card_ids"]) <= 4 for row in ledger["rounds"]))
            self.assertTrue(all(not card_id.startswith("epd_") for row in ledger["rounds"] for card_id in row["card_ids"]))
            self.assertGreater(len(set(card for row in ledger["rounds"] for card in row["card_ids"])), 4)
            feedback = load_json(root / "knowledge" / "feedback.json")
            self.assertTrue(feedback["suppressed_card_ids"])

    def test_incomplete_round_is_not_treated_as_resumable_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            engine = GoalEvolveEngine(self.contract, root, DiversePlanner(), MockEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion())
            incomplete = root / "rounds" / "round_001"
            incomplete.mkdir(parents=True)
            self.assertEqual(engine._last_round(), 0)
            atomic_json(incomplete / "round.json", {"round": 1})
            self.assertEqual(engine._last_round(), 1)

    def test_legacy_import_is_manifest_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            atomic_json(manifest, {"records": [{"design": "unit", "metrics": {"tns_abs_ns": 9.0}, "provenance_grade": "artifact_only"}]})
            records = LegacyImporter().import_manifest(manifest_path=manifest, destination=root / "out.json")
            self.assertEqual(records[0].provenance_grade, "artifact_only")
            self.assertTrue((root / "out.json").is_file())
            atomic_json(manifest, {"records": [{"design": "unit", "metrics": {"tns_abs_ns": 9.0}, "result_path": "/forbidden"}]})
            with self.assertRaises(ValueError):
                LegacyImporter().import_manifest(manifest_path=manifest, destination=root / "bad.json")

    def test_contest_evaluator_writes_official_flow_tcl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark_root = DEFAULT_BENCHMARK_ROOT
            evaluator = Contest2026OpenROADEvaluator(
                Contest2026Config("aes_cipher_top", benchmark_root, DEFAULT_OPENROAD_SEED)
            )
            evaluator._write_tcl(tcl=root / "evaluate.tcl", output=root)
            tcl = (root / "evaluate.tcl").read_text(encoding="utf-8")
            self.assertIn("repair_timing -setup", tcl)
            self.assertIn("write_node_and_net_files", tcl)
            self.assertIn("report_tns", tcl)
            self.assertIn("GOALEVOLVE_CHECKPOINT_METRIC post_route tns_abs_ns", tcl)
            self.assertLess(tcl.rfind("GOALEVOLVE_CHECKPOINT_END post_route"), tcl.rfind("puts \"===== METRICS =====\""))
            placement = tcl.index("detailed_placement\n", tcl.index("set rsz_end"))
            improve = tcl.index("improve_placement", placement)
            mirror = tcl.index("optimize_mirroring", improve)
            final_legalize = tcl.index("detailed_placement\n", mirror)
            self.assertLess(placement, improve)
            self.assertLess(improve, mirror)
            self.assertLess(mirror, final_legalize)
            self.assertLess(final_legalize, tcl.index("check_placement -verbose", final_legalize))

    def test_relocated_cow_build_discards_absolute_cmake_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary) / "build"
            state = build / "CMakeFiles"
            state.mkdir(parents=True)
            (build / "CMakeCache.txt").write_text("CMAKE_HOME_DIRECTORY:INTERNAL=/other/source\n", encoding="utf-8")
            (state / "state.txt").write_text("old", encoding="utf-8")
            Contest2026OpenROADEvaluator._reset_relocated_cmake_state(build)
            self.assertFalse((build / "CMakeCache.txt").exists())
            self.assertFalse(state.exists())

    def test_matching_cow_build_rewrites_cmake_paths_without_discarding_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_source, old_build = root / "seed", root / "seed" / "build_power"
            new_source, new_build = root / "student" / "source", root / "student" / "build"
            old_build.mkdir(parents=True)
            new_build.parent.mkdir(parents=True)
            (old_build / "CMakeCache.txt").write_text(
                f"CMAKE_HOME_DIRECTORY:INTERNAL={old_source}\nCMAKE_CACHEFILE_DIR:INTERNAL={old_build}\n",
                encoding="utf-8",
            )
            (old_build / "CMakeFiles").mkdir()
            (old_build / "CMakeFiles" / "rules.make").write_text(f"{old_source}/src/a.cc {old_build}/src/a.o\n", encoding="utf-8")
            (old_build / "CMakeFiles" / "Makefile2").write_text(f"{old_source} {old_build}\n", encoding="utf-8")
            binary = old_build / "CMakeFiles" / "cached.o"
            binary.write_bytes(b"\x7fELF" + str(old_source).encode("utf-8"))
            shutil.copytree(old_build, new_build)
            self.assertTrue(
                Contest2026OpenROADEvaluator._relocate_cmake_state(
                    build=new_build,
                    old_source=old_source.resolve(),
                    old_build=old_build.resolve(),
                    new_source=new_source.resolve(),
                )
            )
            rendered = (new_build / "CMakeFiles" / "rules.make").read_text(encoding="utf-8")
            self.assertIn(str(new_source.resolve()), rendered)
            self.assertIn(str(new_build.resolve()), rendered)
            self.assertNotIn(str(old_source), (new_build / "CMakeFiles" / "Makefile2").read_text(encoding="utf-8"))
            self.assertTrue((new_build / "CMakeFiles").is_dir())

    def test_no_diff_relocated_baseline_reuses_donor_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed, source = root / "seed", root / "source"
            seed.mkdir()
            shutil.copytree(seed, source)
            build = root / "workspace" / "build_goalevolve"
            (build / "bin").mkdir(parents=True)
            binary = build / "bin" / "openroad"
            binary.write_bytes(b"baseline")
            (build / ".goalevolve_relocated_cow").write_text("test\n", encoding="utf-8")
            evaluator = Contest2026OpenROADEvaluator(
                Contest2026Config("unit", root, source, build_seed_root=seed, allowed_patch_roots=("src/rsz",))
            )
            evaluator._build_relocated_cow = lambda **_: self.fail("no-diff baseline must not rebuild")
            original_seed = evaluator._seed_private_build
            evaluator._seed_private_build = lambda *_args, **_kwargs: True
            try:
                reused = evaluator._build(source, build.parent, configure_log=root / "configure.log", build_log=root / "build.log")
            finally:
                evaluator._seed_private_build = original_seed
            self.assertEqual(reused, binary)
            self.assertIn("no_source_delta", (root / "build.log").read_text(encoding="utf-8"))

    def test_relocated_cow_state_preserves_generated_state_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_source, old_build = root / "seed", root / "seed" / "build_power"
            new_source, new_build = root / "student" / "source", root / "student" / "build"
            old_build.mkdir(parents=True)
            new_build.parent.mkdir(parents=True)
            cache = old_build / "CMakeCache.txt"
            cache.write_text(
                f"CMAKE_HOME_DIRECTORY:INTERNAL={old_source}\nCMAKE_CACHEFILE_DIR:INTERNAL={old_build}\n",
                encoding="utf-8",
            )
            stamp = 1_700_000_000_000_000_000
            os.utime(cache, ns=(stamp, stamp))
            shutil.copytree(old_build, new_build)
            self.assertTrue(
                Contest2026OpenROADEvaluator._relocate_cmake_state(
                    build=new_build,
                    old_source=old_source.resolve(),
                    old_build=old_build.resolve(),
                    new_source=new_source.resolve(),
                )
            )
            self.assertEqual((new_build / "CMakeCache.txt").stat().st_mtime_ns, stamp)

    def test_relocated_cow_state_does_not_make_cached_objects_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "build" / "src" / "CMakeFiles" / "unit.dir"
            target.mkdir(parents=True)
            object_file = target / "unit.cc.o"
            object_file.write_bytes(b"object")
            for state in (target / "flags.make", target / "compiler_depend.ts"):
                state.write_text("state\n", encoding="utf-8")
            Contest2026OpenROADEvaluator._preserve_reused_object_freshness(target.parents[3])
            for state in (target / "flags.make", target / "compiler_depend.ts"):
                self.assertLess(state.stat().st_mtime_ns, object_file.stat().st_mtime_ns)

    def test_private_build_configure_suppresses_second_cmake_regeneration(self) -> None:
        source = inspect.getsource(Contest2026OpenROADEvaluator._build)
        self.assertIn("-DCMAKE_SUPPRESS_REGENERATION=ON", source)
        self.assertIn("if reused_seed:", source)
        self.assertIn("configure skipped", source)
        self.assertIn(".goalevolve_relocated_cow", source)

    def test_relocated_cow_maps_changed_source_and_header_to_direct_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed, candidate, build = root / "seed", root / "candidate", root / "build"
            for source in (seed, candidate):
                (source / "src").mkdir(parents=True)
                (source / "src" / "unit.cc").write_text("int unit() { return 0; }\n", encoding="utf-8")
                (source / "src" / "unit.hh").write_text("int unit();\n", encoding="utf-8")
            (candidate / "src" / "unit.hh").write_text("int unit(int value);\n", encoding="utf-8")
            (build / "CMakeFiles" / "unit.dir").mkdir(parents=True)
            object_file = build / "CMakeFiles" / "unit.dir" / "unit.cc.o"
            object_file.write_bytes(b"object")
            (object_file.with_suffix(".o.d")).write_text(str(candidate / "src" / "unit.hh") + "\n", encoding="utf-8")
            (build / "compile_commands.json").write_text(
                json.dumps([{"file": str(candidate / "src" / "unit.cc"), "output": "CMakeFiles/unit.dir/unit.cc.o"}]),
                encoding="utf-8",
            )
            changed = Contest2026OpenROADEvaluator._changed_cpp_paths(seed=seed, candidate=candidate)
            self.assertEqual(changed, (candidate / "src" / "unit.hh",))
            self.assertEqual(
                Contest2026OpenROADEvaluator._affected_objects(build=build, changed=changed),
                ("CMakeFiles/unit.dir/unit.cc.o",),
            )

    def test_relocated_cow_uses_build_donor_paths_for_promoted_parent_patch(self) -> None:
        """A parent snapshot and a COW build donor need not share a path."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            donor, parent, candidate, build = root / "donor", root / "parent", root / "candidate", root / "build"
            for source in (donor, parent, candidate):
                (source / "src").mkdir(parents=True)
                (source / "src" / "unit.cc").write_text("int unit() { return 0; }\n", encoding="utf-8")
            (candidate / "src" / "unit.cc").write_text("int unit() { return 1; }\n", encoding="utf-8")
            (build / "CMakeFiles" / "unit.dir").mkdir(parents=True)
            object_file = build / "CMakeFiles" / "unit.dir" / "unit.cc.o"
            object_file.write_bytes(b"object")
            (build / "compile_commands.json").write_text(
                json.dumps([{"file": str(donor / "src" / "unit.cc"), "output": "CMakeFiles/unit.dir/unit.cc.o"}]),
                encoding="utf-8",
            )
            changed = Contest2026OpenROADEvaluator._changed_cpp_paths(seed=parent, candidate=candidate)
            self.assertEqual(
                Contest2026OpenROADEvaluator._affected_objects(
                    build=build,
                    changed=changed,
                    seed=parent,
                    candidate=candidate,
                    compile_seed=donor,
                ),
                ("CMakeFiles/unit.dir/unit.cc.o",),
            )

    def test_relocated_cow_builds_local_cmake_object_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary) / "build"
            target = build / "src" / "rsz" / "src"
            (target / "CMakeFiles" / "rsz_lib.dir").mkdir(parents=True)
            (target / "Makefile").write_text("# generated\n", encoding="utf-8")
            groups = Contest2026OpenROADEvaluator._object_build_groups(
                build=build,
                objects=("src/rsz/src/CMakeFiles/rsz_lib.dir/RepairDesign.cc.o",),
            )
            self.assertEqual(groups, ((target, ("RepairDesign.cc.o",)),))
            nested = build / "src" / "grt"
            (nested / "CMakeFiles" / "grt_lib.dir" / "src").mkdir(parents=True)
            (nested / "Makefile").write_text("# generated\n", encoding="utf-8")
            self.assertEqual(
                Contest2026OpenROADEvaluator._object_build_groups(
                    build=build,
                    objects=("src/grt/CMakeFiles/grt_lib.dir/src/GlobalRouter.cpp.o",),
                ),
                ((nested, ("src/GlobalRouter.cpp.o",)),),
            )
            wrapper_target = build / "src" / "grt"
            (wrapper_target / "CMakeFiles" / "grt.dir" / "CMakeFiles" / "grt.dir").mkdir(parents=True)
            self.assertEqual(
                Contest2026OpenROADEvaluator._object_build_groups(
                    build=build,
                    objects=("src/grt/CMakeFiles/grt.dir/CMakeFiles/grt.dir/GlobalRouterTCL_wrap.cxx.o",),
                ),
                ((wrapper_target, ("CMakeFiles/grt.dir/GlobalRouterTCL_wrap.cxx.o",)),),
            )

    def test_relocated_cow_retargets_local_makefile_recursion_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed, source = root / "seed", root / "student" / "source"
            old_build, build = seed / "build_power", root / "student" / "build"
            directory = build / "src" / "rsz" / "src"
            marker = directory / "CMakeFiles" / "rsz_lib.dir"
            marker.mkdir(parents=True)
            old_build.mkdir(parents=True)
            content = f"CMAKE_SOURCE_DIR = {seed}\nCMAKE_BINARY_DIR = {old_build}\n"
            (directory / "Makefile").write_text(content, encoding="utf-8")
            (marker / "build.make").write_text(content, encoding="utf-8")
            evaluator = Contest2026OpenROADEvaluator(
                Contest2026Config("unit", root, seed)
            )
            evaluator._retarget_selected_make_state(
                directory=directory,
                build=build,
                source=source,
            )
            for path in (directory / "Makefile", marker / "build.make"):
                rendered = path.read_text(encoding="utf-8")
                self.assertIn(str(source.resolve()), rendered)
                self.assertIn(str(build.resolve()), rendered)
                self.assertNotIn(str(seed.resolve()), rendered)
                self.assertNotIn(str(old_build.resolve()), rendered)

    def test_relocated_cow_retargets_build_donor_makefile_recursion_entry(self) -> None:
        """A promoted parent may use a different source-only build donor."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent, donor = root / "parent", root / "donor"
            source, build = root / "student" / "source", root / "student" / "build"
            donor_build = donor / "build_power"
            directory = build / "src" / "rsz" / "src"
            marker = directory / "CMakeFiles" / "rsz_lib.dir"
            marker.mkdir(parents=True)
            donor_build.mkdir(parents=True)
            content = f"CMAKE_SOURCE_DIR = {donor}\nCMAKE_BINARY_DIR = {donor_build}\n"
            (directory / "Makefile").write_text(content, encoding="utf-8")
            (marker / "build.make").write_text(content, encoding="utf-8")
            evaluator = Contest2026OpenROADEvaluator(
                Contest2026Config("unit", root, parent, build_seed_root=donor)
            )
            evaluator._retarget_selected_make_state(
                directory=directory,
                build=build,
                source=source,
            )
            for path in (directory / "Makefile", marker / "build.make"):
                rendered = path.read_text(encoding="utf-8")
                self.assertIn(str(source.resolve()), rendered)
                self.assertIn(str(build.resolve()), rendered)
                self.assertNotIn(str(donor.resolve()), rendered)
                self.assertNotIn(str(donor_build.resolve()), rendered)

    def test_relocated_cow_replaces_seed_static_archive_before_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "CMakeFiles" / "unit.dir" / "link.txt"
            script.parent.mkdir(parents=True)
            script.write_text("/usr/bin/ar qc libunit.a unit.cc.o\n/usr/bin/ranlib libunit.a\n", encoding="utf-8")
            archive = root / "libunit.a"
            archive.write_bytes(b"stale")
            Contest2026OpenROADEvaluator._remove_stale_static_archive(link_script=script, cwd=root)
            self.assertFalse(archive.exists())
            executable = root / "openroad_link.txt"
            executable.write_text("/usr/bin/g++ -o openroad unit.cc.o\n", encoding="utf-8")
            archive.write_bytes(b"keep")
            Contest2026OpenROADEvaluator._remove_stale_static_archive(link_script=executable, cwd=root)
            self.assertTrue(archive.exists())

    def test_official_flow_runtime_is_used_when_tool_runtime_is_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metrics_csv = Path(temporary) / "metrics.csv"
            metrics_csv.write_text(
                "tns,leakage_power,slew_over_count,tool_runtime,flow_runtime\n"
                "-9.36,95400000,0,,61\n",
                encoding="utf-8",
            )
            self.assertEqual(_read_metrics(metrics_csv)["runtime_s"], 61.0)

    def test_official_metrics_uses_last_named_design_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metrics_csv = Path(temporary) / "metrics.csv"
            metrics_csv.write_text(
                "design,tns,leakage_power,total_power,slew_over_count,flow_runtime\n"
                ",,,,0,\n"
                "aes_cipher_top,-8.89,68200000,382000000000,0,435\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _read_metrics(metrics_csv),
                {
                    "tns_abs_ns": 8.89,
                    "leakage_power_pw": 68_200_000.0,
                    "drv_count": 0.0,
                    "dynamic_power_pw": 381_931_800_000.0,
                    "runtime_s": 435.0,
                },
            )

    def test_checkpoint_metrics_do_not_need_early_official_report_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "evaluation.log"
            log.write_text(
                "GOALEVOLVE_CHECKPOINT_BEGIN post_route\n"
                "GOALEVOLVE_CHECKPOINT_METRIC post_route tns_abs_ns -12.72\n"
                "GOALEVOLVE_CHECKPOINT_METRIC post_route wns_abs_ns -0.0854\n"
                "GOALEVOLVE_CHECKPOINT_END post_route\n"
                "tns max -12.72\nwns max -0.0854\n",
                encoding="utf-8",
            )
            parsed = _checkpoint_metrics(log)["checkpoints"]
            self.assertEqual(parsed["post_route"]["tns_abs_ns"], 12.72)
            self.assertEqual(parsed["post_route"]["wns_abs_ns"], 0.0854)

    def test_checkpoint_metrics_include_power_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "evaluation.log"
            log.write_text(
                "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_timing\n"
                "Power (Watts)\n"
                "Total 1e-06 2e-06 3e-06 6e-06\n"
                "GOALEVOLVE_CHECKPOINT_END post_repair_timing\n",
                encoding="utf-8",
            )
            parsed = _checkpoint_metrics(log)["checkpoints"]["post_repair_timing"]
            self.assertEqual(parsed["leakage_power_pw"], 3_000_000.0)
            self.assertEqual(parsed["dynamic_power_pw"], 3_000_000.0)

    def test_generated_tcl_sets_time_units_before_pre_repair_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark_root = root / "benchmarks"
            benchmark = benchmark_root / "unit"
            asap7 = root / "asap7"
            benchmark.mkdir(parents=True)
            (asap7 / "lef").mkdir(parents=True)
            (asap7 / "lib").mkdir(parents=True)
            for path in (benchmark / "unit.def", benchmark / "unit.v", benchmark / "unit.sdc", asap7 / "lef" / "unit.lef", asap7 / "lib" / "unit.lib", asap7 / "setRC.tcl"):
                path.write_text("", encoding="utf-8")
            (asap7 / "lib" / "unit_extra.lib").write_text("", encoding="utf-8")
            tcl = root / "evaluate.tcl"
            Contest2026OpenROADEvaluator(Contest2026Config("unit", benchmark_root, root))._write_tcl(tcl=tcl, output=root / "out")
            rendered = tcl.read_text(encoding="utf-8")
            self.assertLess(
                rendered.index("set_cmd_units -time ns"),
                rendered.index("GOALEVOLVE_CHECKPOINT_BEGIN pre_repair"),
            )
            checkpoint_start = rendered.index("GOALEVOLVE_CHECKPOINT_BEGIN pre_repair")
            checkpoint_end = rendered.index("GOALEVOLVE_CHECKPOINT_END pre_repair")
            self.assertLess(checkpoint_start, rendered.index("report_power", checkpoint_start))
            self.assertLess(rendered.index("report_power", checkpoint_start), checkpoint_end)

    def test_generated_tcl_can_export_frozen_tns_goal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark_root = root / "benchmarks"
            benchmark = benchmark_root / "unit"
            asap7 = root / "asap7"
            benchmark.mkdir(parents=True)
            (asap7 / "lef").mkdir(parents=True)
            (asap7 / "lib").mkdir(parents=True)
            for path in (benchmark / "unit.def", benchmark / "unit.v", benchmark / "unit.sdc", asap7 / "lef" / "unit.lef", asap7 / "lib" / "unit.lib", asap7 / "setRC.tcl"):
                path.write_text("", encoding="utf-8")
            tcl = root / "evaluate.tcl"
            Contest2026OpenROADEvaluator(Contest2026Config("unit", benchmark_root, root))._write_tcl(
                tcl=tcl, output=root / "out", goal_tns_abs_ns=11.999
            )
            self.assertIn("set ::env(RSZ_GOAL_TNS_ABS_S) 1.1999e-08", tcl.read_text(encoding="utf-8"))

    def test_power_only_tcl_exports_stage_tns_ceiling_before_repair_power(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark_root = root / "benchmarks"
            benchmark = benchmark_root / "unit"
            asap7 = root / "asap7"
            benchmark.mkdir(parents=True)
            (asap7 / "lef").mkdir(parents=True)
            (asap7 / "lib").mkdir(parents=True)
            for path in (benchmark / "unit.def", benchmark / "unit.v", benchmark / "unit.sdc", asap7 / "lef" / "unit.lef", asap7 / "lib" / "unit.lib", asap7 / "setRC.tcl"):
                path.write_text("", encoding="utf-8")
            tcl = root / "evaluate.tcl"
            evaluator = Contest2026OpenROADEvaluator(
                Contest2026Config(
                    "unit",
                    benchmark_root,
                    root,
                    power_stage_tns_ceiling_ns=200.0,
                )
            )
            evaluator._write_tcl(tcl=tcl, output=root / "out", optimization_mode="power_only")
            rendered = tcl.read_text(encoding="utf-8")
            ceiling = "set ::env(RSZ_POWER_STAGE_TNS_CEILING_S) 2e-07"
            self.assertIn(ceiling, rendered)
            self.assertLess(rendered.index(ceiling), rendered.index("repair_power -phase"))

    def test_rmp_area_power_recipe_runs_after_repair_power_without_repair_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark_root = root / "benchmarks"
            benchmark = benchmark_root / "unit"
            asap7 = root / "asap7"
            benchmark.mkdir(parents=True)
            (asap7 / "lef").mkdir(parents=True)
            (asap7 / "lib").mkdir(parents=True)
            for path in (benchmark / "unit.def", benchmark / "unit.v", benchmark / "unit.sdc", asap7 / "lef" / "unit.lef", asap7 / "setRC.tcl"):
                path.write_text("", encoding="utf-8")
            (asap7 / "lib" / "unit.lib").write_text(
                "library(unit) { cell(A) { area : 1; } }", encoding="utf-8"
            )
            tcl = root / "evaluate.tcl"
            Contest2026OpenROADEvaluator(Contest2026Config("unit", benchmark_root, root))._write_tcl(
                tcl=tcl,
                output=root / "out",
                optimization_mode="power_only",
                timing_recipe_id="rmp_area_power",
            )
            rendered = tcl.read_text(encoding="utf-8")
            self.assertIn("set ::env(RMP_AREA_TELEMETRY) 1", rendered)
            self.assertIn("-target area -slack_threshold 0 -depth_threshold 16", rendered)
            self.assertIn("GOALEVOLVE_CHECKPOINT_BEGIN post_rmp_area_restructure", rendered)
            self.assertLess(rendered.index("repair_power -phase"), rendered.index("restructure -liberty_file"))
            self.assertNotIn("repair_timing -setup", rendered)

    def test_power_planner_assigns_rmp_area_card_its_exact_recipe(self) -> None:
        card = next(
            card
            for card in DiverseRetriever().cards
            if card.card_id == "repair_power_rmp_area_recipe_v1"
        )
        with tempfile.TemporaryDirectory() as temporary:
            plan = DiversePlanner(DiverseRetriever((card,))).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=10,
                student_ids=("student_1",),
                state_root=Path(temporary),
                decision_context={"stage": "power_reclaim", "evaluation_mode": "power_only"},
            )
        self.assertEqual(plan[0].timing_recipe_id, "rmp_area_power")

    def test_rmp_timing_recipe_is_bounded_and_bracketed_by_timing_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark_root = root / "benchmarks"
            benchmark = benchmark_root / "unit"
            asap7 = root / "asap7"
            benchmark.mkdir(parents=True)
            (asap7 / "lef").mkdir(parents=True)
            (asap7 / "lib").mkdir(parents=True)
            for path in (benchmark / "unit.def", benchmark / "unit.v", benchmark / "unit.sdc", asap7 / "lef" / "unit.lef", asap7 / "setRC.tcl"):
                path.write_text("", encoding="utf-8")
            (asap7 / "lib" / "unit.lib").write_text("library(unit) { cell(A) { area : 1; } }", encoding="utf-8")
            (asap7 / "lib" / "unit_extra.lib").write_text("library(unit_extra) { cell(B) { area : 2; } }", encoding="utf-8")
            tcl = root / "evaluate.tcl"
            Contest2026OpenROADEvaluator(Contest2026Config("unit", benchmark_root, root))._write_tcl(
                tcl=tcl,
                output=root / "out",
                optimization_mode="power_then_timing",
                timing_recipe_id="rmp_delay_timing",
            )
            rendered = tcl.read_text(encoding="utf-8")
            self.assertIn("set ::env(RMP_MAX_TRIED_CLOUDS) 4", rendered)
            self.assertIn("set ::env(RMP_MAX_CLOUDS) 4", rendered)
            self.assertIn("set ::env(RMP_ENDPOINT_PATH_COUNT) 4", rendered)
            self.assertIn("set ::env(RMP_UNIQUE_ENDPOINTS) 1", rendered)
            self.assertIn("set ::env(RMP_STA_SELECT_BEST_MODE) 1", rendered)
            self.assertIn("restructure -liberty_file", rendered)
            self.assertIn(f"-liberty_file {{{(root / 'out' / 'rmp_standard_cells.lib').resolve()}}}", rendered)
            manifest = json.loads((root / "out" / "rmp_standard_cells.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["cell_group_count"], 2)
            restructure = rendered.index("restructure -liberty_file")
            timing = rendered.index("repair_timing -setup")
            self.assertLess(restructure, timing)
            self.assertIn("GOALEVOLVE_CHECKPOINT_BEGIN post_rmp_restructure_pre_timing", rendered)

    def test_rmp_path_cone_recipe_records_its_distinct_bounded_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark_root = root / "benchmarks"
            benchmark = benchmark_root / "unit"
            asap7 = root / "asap7"
            benchmark.mkdir(parents=True)
            (asap7 / "lef").mkdir(parents=True)
            (asap7 / "lib").mkdir(parents=True)
            for path in (benchmark / "unit.def", benchmark / "unit.v", benchmark / "unit.sdc", asap7 / "lef" / "unit.lef", asap7 / "setRC.tcl"):
                path.write_text("", encoding="utf-8")
            (asap7 / "lib" / "unit.lib").write_text("library(unit) { cell(A) { area : 1; } }", encoding="utf-8")
            tcl = root / "evaluate.tcl"
            Contest2026OpenROADEvaluator(Contest2026Config("unit", benchmark_root, root))._write_tcl(
                tcl=tcl,
                output=root / "out",
                optimization_mode="power_then_timing",
                timing_recipe_id="rmp_path_cone_timing",
            )
            rendered = tcl.read_text(encoding="utf-8")
            self.assertIn("set ::env(RMP_UNION_ENDPOINT_PATHS) 1", rendered)
            self.assertIn("set ::env(RMP_EXPAND_SIDE_FANIN_LEVELS) 1", rendered)
            self.assertIn("set ::env(RMP_EXPAND_SIDE_FANIN_MAX_ADD) 16", rendered)
            self.assertIn("set ::env(RMP_MAX_TRIED_CLOUDS) 4", rendered)
            Contest2026OpenROADEvaluator(Contest2026Config("unit", benchmark_root, root))._write_tcl(
                tcl=tcl,
                output=root / "out_halo",
                optimization_mode="power_then_timing",
                timing_recipe_id="rmp_path_cone_halo_timing",
            )
            self.assertIn("set ::env(RMP_PATH_CONE_ONLY) 1", tcl.read_text(encoding="utf-8"))

    def test_rmp_combined_liberty_merges_standard_cells_but_not_sram(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ao = root / "asap7sc7p5t_AO_LVT_FF.lib"
            invbuf = root / "asap7sc7p5t_INVBUF_RVT_FF.lib"
            sram = root / "sram_asap7_16x256_1rw.lib"
            ao.write_text('library(AO) { comment : "brace { ignored }"; cell(AO2) { area : 1; } }', encoding="utf-8")
            invbuf.write_text('library(INVBUF) { /* cell(BAD) { } ignored */ cell(INVx6) { area : 2; } }', encoding="utf-8")
            sram.write_text('library(SRAM) { cell(SRAM_CELL) { area : 3; } }', encoding="utf-8")
            combined = _write_rmp_combined_liberty((ao, invbuf, sram), root / "out")
            merged = combined.read_text(encoding="utf-8")
            self.assertIn("cell(AO2)", merged)
            self.assertIn("cell(INVx6)", merged)
            self.assertNotIn("cell(BAD)", merged)
            self.assertNotIn("SRAM_CELL", merged)
            self.assertEqual(len(_liberty_cell_blocks(merged)), 2)
            manifest = json.loads((root / "out" / "rmp_standard_cells.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["cell_group_count"], 2)

    def test_tns_global_recipe_explicitly_activates_its_source_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark_root = root / "benchmarks"
            benchmark = benchmark_root / "unit"
            asap7 = root / "asap7"
            benchmark.mkdir(parents=True)
            (asap7 / "lef").mkdir(parents=True)
            (asap7 / "lib").mkdir(parents=True)
            for path in (benchmark / "unit.def", benchmark / "unit.v", benchmark / "unit.sdc", asap7 / "lef" / "unit.lef", asap7 / "lib" / "unit.lib", asap7 / "setRC.tcl"):
                path.write_text("", encoding="utf-8")
            tcl = root / "evaluate.tcl"
            Contest2026OpenROADEvaluator(Contest2026Config("unit", benchmark_root, root))._write_tcl(
                tcl=tcl,
                output=root / "out",
                optimization_mode="timing_only",
                timing_recipe_id="tns_global",
            )
            rendered = tcl.read_text(encoding="utf-8")
            self.assertLess(
                rendered.index("set ::env(RSZ_ENABLE_TNS_ENDPOINT_FRONTIER) 1"),
                rendered.index("repair_timing -setup -phases {TNS LAST_GASP CRIT_VT_SWAP}"),
            )

    def test_generated_openroad_version_header_is_not_a_candidate_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed, candidate = root / "seed", root / "candidate"
            for source in (seed, candidate):
                (source / "include/ord").mkdir(parents=True)
                (source / "src/rsz/src").mkdir(parents=True)
                (source / "include/ord/Version.hh").write_text('#define OPENROAD_VERSION "base"\n', encoding="utf-8")
                (source / "src/rsz/src/RecoverPower.cc").write_text("void recover() {}\n", encoding="utf-8")
            (candidate / "include/ord/Version.hh").write_text('#define OPENROAD_VERSION "generated"\n', encoding="utf-8")
            (candidate / "src/rsz/src/RecoverPower.cc").write_text("void recover() { improve(); }\n", encoding="utf-8")
            diff = _source_diff(seed, candidate)
            self.assertIn("RecoverPower.cc", diff)
            self.assertNotIn("Version.hh", diff)

    def test_contest_config_defaults_to_openroad_power(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contest.json"
            atomic_json(path, {
                "design": "aes_cipher_top",
                "state_root": "state",
                "baseline_metrics": {"tns_abs_ns": 1.0},
                "target_metrics": {"tns_abs_ns": 1.0},
                "evaluator": "contest_openroad",
            })
            self.assertEqual(load_config(path).source_root, DEFAULT_OPENROAD_SEED)
            self.assertTrue(DEFAULT_OPENROAD_SEED.is_dir())

    def test_repository_graph_profile_default_and_cli_override(self) -> None:
        from goalevolve.cli import build_parser

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contest.json"
            atomic_json(path, {
                "design": "aes_cipher_top",
                "state_root": "state",
                "baseline_metrics": {"tns_abs_ns": 1.0},
                "target_metrics": {"tns_abs_ns": 1.0},
                "evaluator": "contest_openroad",
            })
            self.assertTrue(load_config(path).repository_graph_enabled)
            atomic_json(path, {
                "design": "aes_cipher_top",
                "state_root": "state",
                "baseline_metrics": {"tns_abs_ns": 1.0},
                "target_metrics": {"tns_abs_ns": 1.0},
                "evaluator": "contest_openroad",
                "repository_graph_enabled": False,
            })
            self.assertFalse(load_config(path).repository_graph_enabled)

        parser = build_parser()
        self.assertIsNone(parser.parse_args(["run", "--config", "campaign.json"]).repository_graph)
        self.assertEqual(
            parser.parse_args(
                ["run", "--config", "campaign.json", "--repository-graph", "off"]
            ).repository_graph,
            "off",
        )

    def test_disabled_repository_graph_does_not_construct_an_index(self) -> None:
        engine = GoalEvolveEngine(
            self.contract,
            Path("state"),
            DiversePlanner(),
            MockEvaluator(),
            IsolatedWorkspace(),
            StrictEvidencePromotion(),
            repository_graph_enabled=False,
        )
        with patch("goalevolve.execution.engine.RepositoryGraphIndex") as graph_index:
            graph = engine._parent_repository_graph(
                source_root=Path("source"),
                source_hash="parent_hash",
                allowed_patch_roots=("src/rsz",),
            )
        self.assertIsNone(graph)
        graph_index.assert_not_called()

    def test_graph_free_engine_records_its_effective_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            engine = GoalEvolveEngine(
                self.contract,
                root,
                DiversePlanner(),
                MockEvaluator(),
                IsolatedWorkspace(),
                StrictEvidencePromotion(),
                repository_graph_enabled=False,
            )
            engine.initialize(baseline_metrics=dict(self.contract.baseline_metrics))

            self.assertFalse(load_json(root / "plugins.json")["repository_graph_enabled"])
            self.assertFalse(
                load_json(root / "runtime_provenance.json")["entries"][0]["repository_graph_enabled"]
            )

    def test_portable_design_profiles_resolve_only_project_assets(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        expected_evolution_designs = {
            "aes_cipher_top",
            "ariane",
            "jpeg_encoder",
            "mempool_group",
            "nvdla_a",
            "nvdla_c",
            "nvdla_m",
            "nvdla_p",
        }
        ready_designs = {"aes_cipher_top", "jpeg_encoder"}
        profiles = sorted((project_root / "experiments").glob("*/evolve.json"))
        self.assertEqual({load_config(path).design for path in profiles}, expected_evolution_designs)
        for profile in profiles:
            config = load_config(profile)
            self.assertEqual(config.codex.credential_env, DEFAULT_CREDENTIAL_ENV)
            self.assertEqual(config.codex.student.model, "gpt-5.6-terra")
            self.assertEqual(config.codex.teacher.model, "gpt-5.6-terra")
            self.assertEqual(config.codex.teacher.max_plan_format_repairs, 2)
            self.assertEqual(config.state_root, project_root / "outputs" / "ae3" / config.design)
            self.assertEqual(config.campaign_ready, config.design in ready_designs)
            self.assertTrue(config.source_root and config.source_root.is_dir())
            self.assertTrue((config.source_root / "CMakeLists.txt").is_file())
            benchmark = config.benchmark_root / config.design
            self.assertTrue((benchmark / f"{config.design}.def.gz").is_file())
            self.assertTrue((benchmark / f"{config.design}.v").is_file())
            self.assertTrue((benchmark / f"{config.design}.sdc").is_file())
            self.assertTrue((benchmark / "metrics.csv").is_file())

        expected_baseline_designs = expected_evolution_designs - {"aes_cipher_top"}
        baseline_profiles = sorted((project_root / "experiments").glob("*/baseline.json"))
        self.assertEqual({load_config(path).design for path in baseline_profiles}, expected_baseline_designs)
        for profile in baseline_profiles:
            config = load_config(profile)
            self.assertFalse(config.campaign_ready)
            self.assertEqual(config.state_root, project_root / "outputs" / "baseline" / config.design)

    def test_baseline_builds_a_private_clone_of_the_shared_source_snapshot(self) -> None:
        source = inspect.getsource(Contest2026OpenROADEvaluator.evaluate_baseline)
        self.assertIn("shutil.rmtree(workspace)", source)
        self.assertIn("clone_source_tree(self.config.source_seed, source)", source)
        self.assertIn("self._build(\n                source,", source)

    def test_baseline_cli_is_registered(self) -> None:
        from goalevolve.cli import build_parser

        args = build_parser().parse_args(["baseline", "--config", "campaign.json"])
        self.assertEqual(args.command, "baseline")

    def test_phase_signal_requires_source_emitted_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "evaluation.log"
            log.write_text("METRIC|accepted_commit|2\nMETRIC|other_signal|9\n", encoding="utf-8")
            self.assertEqual(_observed_phase_signals(log, ("accepted_commit", "missing")), {"accepted_commit": 2.0})

    def test_checkpoint_parser_and_epd_v2_lifecycle_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "evaluation.log"
            log.write_text(
                "GOALEVOLVE_CHECKPOINT_BEGIN post_repair_timing\n"
                "tns max -2.5\nwns max -0.2\nTotal 1.0e-2 2.0e-2 3.0e-5 3.1e-2\n"
                "GOALEVOLVE_CHECKPOINT_END post_repair_timing\n",
                encoding="utf-8",
            )
            parsed = _checkpoint_metrics(log)
            self.assertEqual(parsed["checkpoints"]["post_repair_timing"]["tns_abs_ns"], 2.5)
            self.assertEqual(parsed["checkpoints"]["post_repair_timing"]["leakage_power_pw"], 3.0e7)
            db = EvolutionProgramDatabase(root)
            pending_idea_id, promising_idea_id, invalid_idea_id = db.register_teacher_ideas(
                round_index=1,
                parent=self.parent,
                evolution_ideas=(
                    {
                        "idea": "A ranked but not yet executed timing experiment.",
                        "source_hooks": ("src/rsz/src/RecoverPower.cc",),
                        "expected_signals": ("accepted",),
                    },
                    {
                        "idea": "A telemetry-incomplete timing experiment.",
                        "source_hooks": ("src/rsz/src/RecoverPower.cc",),
                        "expected_signals": ("accepted",),
                    },
                    {
                        "idea": "A refuted timing experiment.",
                        "source_hooks": ("src/rsz/src/RecoverPower.cc",),
                        "expected_signals": ("accepted",),
                    },
                ),
            )
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            cases = [
                ("validated", CandidateResult("s", self.hypothesis, {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0}, {"accepted": 1.0}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+x\n", "v")),
                ("promising", CandidateResult("s", replace(self.hypothesis, epd_idea_id=promising_idea_id), {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0}, {}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+y\n", "p")),
                ("invalid", CandidateResult("s", replace(self.hypothesis, epd_idea_id=invalid_idea_id), {"tns_abs_ns": 110.0, "leakage_power_pw": 220.0}, {"accepted": 1.0}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+w\n", "i")),
            ]
            for _, candidate in cases:
                verdict = classify_candidate(contract=self.contract, parent=self.parent, candidate=candidate)
                db.record(round_index=1, parent=self.parent, candidate=candidate, verdict=verdict)
            statuses = {row["epd_status"] for row in db.records()}
            self.assertEqual(statuses, {"validated", "promising", "invalid"})
            self.assertEqual(db.idea(pending_idea_id)["status"], "pending")
            self.assertEqual(
                {status for status, count in db.summary()["status_counts"].items() if count},
                {"validated", "promising", "pending", "invalid"},
            )
            db.ensure_baseline(self.parent)
            db.attach_baseline_evaluation(
                parent=self.parent,
                metrics={"tns_abs_ns": 100.0, "leakage_power_pw": 200.0},
                goal_distance=self.parent.goal_distance,
                artifacts={"checkpoint_metrics": str(root / "missing.json")},
                passed=True,
            )
            baseline = next(row for row in db.records() if row["hypothesis_id"] == "baseline")
            self.assertEqual(baseline["epd_status"], "validated")
            self.assertEqual(baseline["evidence_state"], "baseline_measured_4of4")

    def test_epd_keeps_a_complete_but_unactivated_attempt_retriable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = EvolutionProgramDatabase(root)
            idea_id = db.register_teacher_ideas(
                round_index=2,
                parent=self.parent,
                evolution_ideas=(
                    {
                        "idea": "Run a bounded phase-specific timing decision.",
                        "source_hooks": ("src/rsz/src/RecoverPower.cc",),
                        "expected_signals": ("phase_specific_examined",),
                    },
                ),
            )[0]
            candidate = CandidateResult(
                "student_1",
                replace(
                    self.hypothesis,
                    epd_idea_id=idea_id,
                    expected_signals=("phase_specific_examined",),
                ),
                {"tns_abs_ns": 110.0, "leakage_power_pw": 220.0},
                {},
                [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                "+++ b/src/rsz/src/RecoverPower.cc\n+phase decision\n",
                "unactivated-source",
            )
            verdict = classify_candidate(
                contract=self.contract,
                parent=self.parent,
                candidate=candidate,
            )
            record = db.record(
                round_index=2,
                parent=self.parent,
                candidate=candidate,
                verdict=verdict,
            )
            idea = db.idea(idea_id)
            teacher = db.teacher_summary()
        self.assertEqual(verdict.state, "refuted")
        self.assertFalse(verdict.mechanism_fired)
        self.assertEqual(record.epd_status, "unactivated")
        self.assertEqual(idea["status"], "unactivated")
        self.assertEqual(teacher["status_counts"]["unactivated"], 1)

    def test_epd_reclassifies_historical_unactivated_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = EvolutionProgramDatabase(root)
            idea_id = db.register_teacher_ideas(
                round_index=2,
                parent=self.parent,
                evolution_ideas=(
                    {
                        "idea": "Run a historical phase-specific timing decision.",
                        "source_hooks": ("src/rsz/src/RecoverPower.cc",),
                        "expected_signals": ("phase_specific_examined",),
                    },
                ),
            )[0]
            candidate = CandidateResult(
                "student_1",
                replace(
                    self.hypothesis,
                    epd_idea_id=idea_id,
                    expected_signals=("phase_specific_examined",),
                ),
                {"tns_abs_ns": 110.0, "leakage_power_pw": 220.0},
                {},
                [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                "+++ b/src/rsz/src/RecoverPower.cc\n+phase decision\n",
                "historical-unactivated-source",
            )
            record = db.record(
                round_index=2,
                parent=self.parent,
                candidate=candidate,
                verdict=classify_candidate(
                    contract=self.contract,
                    parent=self.parent,
                    candidate=candidate,
                ),
            )
            payload = load_json(root / "knowledge" / "epd.json")
            payload["attempts"][0]["epd_status"] = "invalid"
            payload["ideas"][0]["status"] = "invalid"
            atomic_json(root / "knowledge" / "epd.json", payload)
            reclassified = db.reclassify_historical_unactivated_attempts()
            idea_status = db.idea(idea_id)["status"]
        self.assertEqual(reclassified, (record.record_id,))
        self.assertEqual(idea_status, "unactivated")

    def test_previous_teacher_review_does_not_resuppress_an_unactivated_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = EvolutionProgramDatabase(root)
            idea_id = db.register_teacher_ideas(
                round_index=1,
                parent=self.parent,
                evolution_ideas=(
                    {
                        "idea": "Run a bounded phase-specific timing decision.",
                        "source_hooks": ("src/rsz/src/RecoverPower.cc",),
                        "expected_signals": ("phase_specific_examined",),
                    },
                ),
            )[0]
            hypothesis = replace(
                self.hypothesis,
                hypothesis_id="r001_student_1_phase_specific",
                epd_idea_id=idea_id,
                expected_signals=("phase_specific_examined",),
            )
            candidate = CandidateResult(
                "student_1",
                hypothesis,
                {"tns_abs_ns": 110.0, "leakage_power_pw": 220.0},
                {},
                [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                "+++ b/src/rsz/src/RecoverPower.cc\n+phase decision\n",
                "unactivated-review-source",
            )
            verdict = classify_candidate(
                contract=self.contract,
                parent=self.parent,
                candidate=candidate,
            )
            db.record(round_index=1, parent=self.parent, candidate=candidate, verdict=verdict)
            review_path = root / "rounds" / "round_001" / "teacher_review.json"
            review_path.parent.mkdir(parents=True)
            atomic_json(
                review_path,
                {
                    "teacher_ok": True,
                    "teacher_markdown": "## Mechanism Actions\n### phase_specific\n- Action: suppress",
                    "parsed_markdown": {"mechanism_actions": [{"action": "suppress"}]},
                    "outcomes": [
                        {
                            "student_id": "student_1",
                            "hypothesis_id": hypothesis.hypothesis_id,
                            "mechanism_family": hypothesis.mechanism_family,
                            "hypothesis": hypothesis.to_dict(),
                            "verdict": verdict.to_dict(),
                        }
                    ],
                },
            )
            atomic_json(
                review_path.parent / "round.json",
                {"promoted_student": None, "parent_after": self.parent.to_dict()},
            )
            engine = SimpleNamespace(state_root=root, _epd=lambda: db)
            guidance = GoalEvolveEngine._previous_teacher_review(
                engine,
                2,
                parent=self.parent,
            )
        self.assertEqual(guidance["teacher_markdown"], "")
        self.assertTrue(guidance["raw_review"]["superseded"])
        self.assertEqual(guidance["outcomes"][0]["epd_lifecycle"], "unactivated")

    def test_codex_student_command_and_packet_are_source_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prompt = root / "student.md"
            prompt.write_text("packet", encoding="utf-8")
            source = root / "workspace" / "source"
            source.mkdir(parents=True)
            editor = CodexStudentEditor(CodexStudentConfig(allowed_patch_roots=("src/rsz",)))
            command = PersistentCodexRunner(CodexRuntimeConfig())._command(cwd=source, final_message=root / "last.md", prior_thread="")
            self.assertEqual(command[:3], ["codex", "exec", "--json"])
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertIn("plugins", command)
            self.assertNotIn("--sandbox", command)
            resumed = PersistentCodexRunner(CodexRuntimeConfig())._command(cwd=source, final_message=root / "last.md", prior_thread="thread-id")
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", resumed)
            rendered = editor._execution_prompt(
                prompt_path=prompt,
                source=source,
                parent=self.parent,
                hypothesis=self.hypothesis,
            )
            self.assertIn(str(source), rendered)
            self.assertIn("src/rsz", rendered)
            self.assertIn("METRIC|<expected_signal>|<nonzero-number>", rendered)
            self.assertIn("Do not run git init", rendered)
            self.assertIn("sibling source.git", rendered)
            self.assertIn("Do not reformat an entire file", rendered)
            repair = editor._repair_prompt(
                prompt_path=prompt,
                source=source,
                parent=self.parent,
                hypothesis=self.hypothesis,
                failure_context="candidate_build_failed",
                repair_attempt=1,
            )
            self.assertIn("whole-source integration failure", repair)
            self.assertIn("search every allowed source file", repair)
            bounded_repair = editor._repair_prompt(
                prompt_path=prompt,
                source=source,
                parent=self.parent,
                hypothesis=replace(
                    self.hypothesis,
                    allowed_patch_paths=("src/rsz/src/RecoverPower.cc",),
                ),
                failure_context="candidate_build_failed",
                repair_attempt=1,
            )
            self.assertIn(
                "Exact repair patch boundary: `src/rsz/src/RecoverPower.cc`.",
                bounded_repair,
            )
            self.assertIn("Any change outside this boundary is rejected", bounded_repair)
            self.assertTrue(NoopStudentEditor().apply().ok)

    def test_codex_remote_context_is_round_scoped_but_repair_continuous(self) -> None:
        self.assertEqual(CodexStudentEditor._round_identity("student_1", 48), "student_1_r048")
        self.assertNotEqual(
            CodexStudentEditor._round_identity("student_1", 48),
            CodexStudentEditor._round_identity("student_1", 49),
        )
        self.assertEqual(CodexTeacher._round_identity(48), "teacher_r048")
        self.assertNotEqual(CodexTeacher._round_identity(48), CodexTeacher._round_identity(49))

    def test_student_vcs_metadata_is_removed_before_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            (workspace / "source" / ".git").mkdir(parents=True)
            (workspace / "source.git").mkdir()
            removed = CodexStudentEditor._remove_student_vcs(workspace)
            self.assertEqual(set(removed), {"source/.git", "source.git"})
            self.assertFalse((workspace / "source" / ".git").exists())
            self.assertFalse((workspace / "source.git").exists())

    def test_project_codex_credentials_are_isolated_from_user_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            credential_env = root / "goalevolve_codex.env"
            credential_env.write_text(
                "GOALEVOLVE_OPENAI_API_KEY=project-key-for-test\nGOALEVOLVE_PROVIDER_NAME=OpenAI\nGOALEVOLVE_BASE_URL=https://provider.invalid/v1\nGOALEVOLVE_WIRE_API=responses\n",
                encoding="utf-8",
            )
            runner = PersistentCodexRunner(CodexRuntimeConfig(seed_home=root / "unused", credential_env=credential_env))
            work = root / "work"
            work.mkdir()
            home = runner._ensure_home(root / "state", "student_1")
            environment = runner._environment(home=home, cwd=work)
            self.assertEqual(environment["CODEX_HOME"], str(home))
            self.assertEqual(environment["HOME"], str(home))
            self.assertEqual(environment["OPENAI_API_KEY"], "project-key-for-test")
            self.assertFalse(any(key.startswith("GOALEVOLVE_") for key in environment))
            self.assertTrue((home / ".goalevolve_project_credentials").is_file())
            self.assertIn("model_provider", (home / "config.toml").read_text(encoding="utf-8"))

    def test_project_codex_credentials_refresh_for_resumed_student(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            credential_env = root / "goalevolve_codex.env"
            credential_env.write_text(
                "GOALEVOLVE_OPENAI_API_KEY=first-project-key\nGOALEVOLVE_PROVIDER_NAME=OpenAI\n"
                "GOALEVOLVE_BASE_URL=https://provider.invalid/v1\nGOALEVOLVE_WIRE_API=responses\n",
                encoding="utf-8",
            )
            runner = PersistentCodexRunner(CodexRuntimeConfig(seed_home=root / "unused", credential_env=credential_env))
            home = runner._ensure_home(root / "state", "student_1")
            self.assertIn("first-project-key", (home / "auth.json").read_text(encoding="utf-8"))
            credential_env.write_text(
                "GOALEVOLVE_OPENAI_API_KEY=rotated-project-key\nGOALEVOLVE_PROVIDER_NAME=OpenAI\n"
                "GOALEVOLVE_BASE_URL=https://provider.invalid/v1\nGOALEVOLVE_WIRE_API=responses\n",
                encoding="utf-8",
            )
            self.assertEqual(runner._ensure_home(root / "state", "student_1"), home)
            self.assertIn("rotated-project-key", (home / "auth.json").read_text(encoding="utf-8"))

    def test_same_student_repairs_build_failure_before_final_evidence(self) -> None:
        class RepairingEditor:
            name = "repairing_editor"
            config = SimpleNamespace(max_repair_attempts=2)

            def __init__(self) -> None:
                self.repair_students: list[str] = []
                self.asserted_context = ""

            def apply(self, **_: object) -> StudentEditReport:
                return StudentEditReport(True, "edited", "initial", "thread-1", {})

            def repair(self, *, student_id: str, failure_context: str, repair_attempt: int, **_: object) -> StudentEditReport:
                self.repair_students.append(student_id)
                self.asserted_context = failure_context
                return StudentEditReport(True, "repaired", f"repair-{repair_attempt}", "thread-1", {"repair_note": "same-thread"})

        class FailThenPass:
            name = "fail_then_pass"

            def __init__(self, hypothesis: Hypothesis, log: Path) -> None:
                self.calls = 0
                self.hypothesis = hypothesis
                self.log = log

            def evaluate(self, *, student_id: str, **_: object) -> CandidateResult:
                self.calls += 1
                diff = "+++ b/src/rsz/src/RecoverPower.cc\n+repair\n"
                if self.calls == 1:
                    return CandidateResult(student_id, self.hypothesis, {}, {}, [], diff, "broken", artifacts={"build_log": str(self.log)}, evaluation_error="RuntimeError:candidate_build_failed:1")
                checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
                return CandidateResult(student_id, self.hypothesis, {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0}, {"accepted": 1.0}, checks, diff, "fixed")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "source").mkdir(parents=True)
            prompt = root / "prompt.md"
            prompt.write_text("packet", encoding="utf-8")
            build_log = root / "build.log"
            build_log.write_text("error: undeclared identifier\n", encoding="utf-8")
            editor = RepairingEditor()
            evaluator = FailThenPass(self.hypothesis, build_log)
            engine = GoalEvolveEngine(self.contract, root / "state", DiversePlanner(), evaluator, IsolatedWorkspace(), StrictEvidencePromotion(), student_editor=editor)
            candidate = engine._edit_then_evaluate(self.parent, self.hypothesis, "student_1", workspace, prompt, 1)
            self.assertEqual(evaluator.calls, 2)
            self.assertEqual(editor.repair_students, ["student_1"])
            self.assertIsNone(candidate.evaluation_error)
            self.assertIn("error: undeclared identifier", editor.asserted_context)
            self.assertIn("repair_01_repair_note", candidate.artifacts)
            self.assertTrue((workspace.parent / "artifacts" / "repair_attempts" / "engineering" / "attempt_01" / "evaluation" / "candidate_before_repair.json").is_file())

    def test_engineering_repair_stays_within_initial_changed_files(self) -> None:
        class RepairEditor:
            name = "repair_editor"
            config = SimpleNamespace(max_repair_attempts=1)

            def __init__(self) -> None:
                self.repair_boundaries: list[tuple[str, ...]] = []

            def apply(self, **_: object) -> StudentEditReport:
                return StudentEditReport(True, "edited", "initial", "thread-1", {})

            def repair(self, *, hypothesis: Hypothesis, **_: object) -> StudentEditReport:
                self.repair_boundaries.append(hypothesis.allowed_patch_paths)
                return StudentEditReport(True, "repaired", "engineering", "thread-1", {})

        class EngineeringScopeEvaluator:
            name = "engineering_scope"

            def __init__(self, hypothesis: Hypothesis, parent_metrics: dict[str, float]) -> None:
                self.calls = 0
                self.hypothesis = hypothesis
                self.parent_metrics = parent_metrics

            def evaluate(self, *, student_id: str, hypothesis: Hypothesis, **_: object) -> CandidateResult:
                self.calls += 1
                diff = "+++ b/src/rmp/src/Restructure.cpp\n+RMP change\n"
                if self.calls == 1:
                    return CandidateResult(
                        student_id,
                        hypothesis,
                        {},
                        {},
                        [],
                        diff,
                        "broken",
                        evaluation_error="RuntimeError:candidate_build_failed:1",
                    )
                diff += "+++ b/src/rsz/src/policy/RepairPowerPolicy.cc\n+cross-mechanism repair\n"
                checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
                candidate = CandidateResult(
                    student_id,
                    hypothesis,
                    dict(self.parent_metrics),
                    {"accepted": 1.0},
                    checks,
                    diff,
                    "cross-mechanism",
                )
                report = preflight_candidate(
                    candidate,
                    allowed_patch_paths=hypothesis.allowed_patch_paths,
                )
                if not report.ok:
                    candidate.evaluation_error = ";".join(report.violations)
                return candidate

        hypothesis = Hypothesis(
            "rmp",
            "rmp_area",
            "claim",
            ("src/rmp/src/Restructure.cpp",),
            ("accepted",),
            ("rmp",),
            "rmp",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "source").mkdir(parents=True)
            prompt = root / "prompt.md"
            prompt.write_text("packet", encoding="utf-8")
            editor = RepairEditor()
            evaluator = EngineeringScopeEvaluator(hypothesis, dict(self.parent.metrics))
            engine = GoalEvolveEngine(
                self.contract,
                root / "state",
                DiversePlanner(),
                evaluator,
                IsolatedWorkspace(),
                StrictEvidencePromotion(),
                student_editor=editor,
            )

            candidate = engine._edit_then_evaluate(
                self.parent, hypothesis, "student_1", workspace, prompt, 1
            )

            self.assertEqual(editor.repair_boundaries, [("src/rmp/src/Restructure.cpp",)])
            self.assertIn(
                "outside_assigned_patch_scope:src/rsz/src/policy/RepairPowerPolicy.cc",
                candidate.evaluation_error or "",
            )

    def test_missing_telemetry_repairs_same_student_without_losing_better_qor(self) -> None:
        class TelemetryEditor:
            name = "telemetry_editor"
            config = SimpleNamespace(max_repair_attempts=0)

            def __init__(self) -> None:
                self.repair_kinds: list[str] = []

            def apply(self, *, workspace: Path, **_: object) -> StudentEditReport:
                (workspace / "source" / "policy.cc").write_text("before telemetry repair\n", encoding="utf-8")
                return StudentEditReport(True, "edited", "initial", "thread-1", {})

            def repair(self, *, workspace: Path, repair_kind: str, **_: object) -> StudentEditReport:
                self.repair_kinds.append(repair_kind)
                (workspace / "source" / "policy.cc").write_text("after telemetry repair\n", encoding="utf-8")
                return StudentEditReport(True, "instrumented", "telemetry-repair", "thread-1", {"telemetry_note": "real-boundary"})

        class QoRThenTelemetry:
            name = "qor_then_telemetry"

            def __init__(self, hypothesis: Hypothesis) -> None:
                self.calls = 0
                self.hypothesis = hypothesis

            def evaluate(self, *, student_id: str, workspace: Path, **_: object) -> CandidateResult:
                self.calls += 1
                checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
                if self.calls == 1:
                    return CandidateResult(student_id, self.hypothesis, {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0}, {}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+policy\n", "first", artifacts={"candidate_source": str(workspace / "source")})
                return CandidateResult(student_id, self.hypothesis, {"tns_abs_ns": 65.0, "leakage_power_pw": 175.0}, {"accepted": 1.0}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+policy and telemetry\n", "instrumented", artifacts={"candidate_source": str(workspace / "source")})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "source").mkdir(parents=True)
            prompt = root / "prompt.md"
            prompt.write_text("packet", encoding="utf-8")
            editor = TelemetryEditor()
            evaluator = QoRThenTelemetry(self.hypothesis)
            engine = GoalEvolveEngine(self.contract, root / "state", DiversePlanner(), evaluator, IsolatedWorkspace(), StrictEvidencePromotion(), student_editor=editor)
            candidate = engine._edit_then_evaluate(self.parent, self.hypothesis, "student_1", workspace, prompt, 1)
            self.assertEqual(evaluator.calls, 2)
            self.assertEqual(editor.repair_kinds, ["telemetry"])
            self.assertEqual(candidate.metrics["tns_abs_ns"], 60.0)
            self.assertEqual(classify_candidate(contract=self.contract, parent=self.parent, candidate=candidate).state, "verified_qor_unattributed")
            self.assertIn("repaired_evaluation_worse", candidate.artifacts["telemetry_repair_outcome"])
            preserved_source = Path(candidate.artifacts["candidate_source"])
            self.assertIn("before telemetry repair", (preserved_source / "policy.cc").read_text(encoding="utf-8"))
            self.assertIn("after telemetry repair", (workspace / "source" / "policy.cc").read_text(encoding="utf-8"))
            self.assertTrue(Path(candidate.artifacts["telemetry_repair_candidate"]).is_file())
            self.assertTrue((workspace.parent / "artifacts" / "repair_attempts" / "telemetry" / "attempt_01" / "evaluation" / "candidate_before_repair.json").is_file())

    def test_missing_telemetry_repairs_same_round_even_without_qor_gain(self) -> None:
        class TelemetryEditor:
            name = "telemetry_editor"
            config = SimpleNamespace(max_repair_attempts=0)

            def __init__(self) -> None:
                self.repair_kinds: list[str] = []

            def apply(self, **_: object) -> StudentEditReport:
                return StudentEditReport(True, "edited", "initial", "thread-1", {})

            def repair(self, *, repair_kind: str, **_: object) -> StudentEditReport:
                self.repair_kinds.append(repair_kind)
                return StudentEditReport(True, "activated", "telemetry-repair", "thread-1", {})

        class NoGainThenActivated:
            name = "no_gain_then_activated"

            def __init__(self, hypothesis: Hypothesis) -> None:
                self.calls = 0
                self.hypothesis = hypothesis

            def evaluate(self, *, student_id: str, **_: object) -> CandidateResult:
                self.calls += 1
                checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
                signals = {} if self.calls == 1 else {"accepted": 1.0}
                return CandidateResult(
                    student_id,
                    self.hypothesis,
                    dict(self.parent_metrics),
                    signals,
                    checks,
                    "+++ b/src/rsz/src/RecoverPower.cc\n+policy\n",
                    f"commit-{self.calls}",
                )

            parent_metrics: dict[str, float] = {}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "source").mkdir(parents=True)
            prompt = root / "prompt.md"
            prompt.write_text("packet", encoding="utf-8")
            editor = TelemetryEditor()
            evaluator = NoGainThenActivated(self.hypothesis)
            evaluator.parent_metrics = dict(self.parent.metrics)
            engine = GoalEvolveEngine(self.contract, root / "state", DiversePlanner(), evaluator, IsolatedWorkspace(), StrictEvidencePromotion(), student_editor=editor)
            candidate = engine._edit_then_evaluate(self.parent, self.hypothesis, "student_1", workspace, prompt, 1)
            self.assertEqual(evaluator.calls, 2)
            self.assertEqual(editor.repair_kinds, ["telemetry"])
            self.assertEqual(candidate.phase_signals, {"accepted": 1.0})

    def test_telemetry_repair_stays_within_initial_changed_files(self) -> None:
        class RepairEditor:
            name = "repair_editor"
            config = SimpleNamespace(max_repair_attempts=0)

            def __init__(self) -> None:
                self.repair_boundaries: list[tuple[str, ...]] = []

            def apply(self, **_: object) -> StudentEditReport:
                return StudentEditReport(True, "edited", "initial", "thread-1", {})

            def repair(self, *, hypothesis: Hypothesis, **_: object) -> StudentEditReport:
                self.repair_boundaries.append(hypothesis.allowed_patch_paths)
                return StudentEditReport(True, "instrumented", "telemetry", "thread-1", {})

        class ScopeEnforcingEvaluator:
            name = "scope_enforcing"

            def __init__(self, hypothesis: Hypothesis, parent_metrics: dict[str, float]) -> None:
                self.calls = 0
                self.hypothesis = hypothesis
                self.parent_metrics = parent_metrics

            def evaluate(self, *, student_id: str, hypothesis: Hypothesis, **_: object) -> CandidateResult:
                self.calls += 1
                checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
                diff = "+++ b/src/rmp/src/Restructure.cpp\n+RMP change\n"
                if self.calls == 2:
                    diff += "+++ b/src/rsz/src/policy/RepairPowerPolicy.cc\n+cross-mechanism repair\n"
                candidate = CandidateResult(
                    student_id,
                    hypothesis,
                    dict(self.parent_metrics),
                    {} if self.calls == 1 else {"accepted": 1.0},
                    checks,
                    diff,
                    f"commit-{self.calls}",
                )
                report = preflight_candidate(
                    candidate,
                    allowed_patch_paths=hypothesis.allowed_patch_paths,
                )
                if not report.ok:
                    candidate.evaluation_error = ";".join(report.violations)
                return candidate

        hypothesis = Hypothesis(
            "rmp",
            "rmp_area",
            "claim",
            ("src/rmp/src/Restructure.cpp",),
            ("accepted",),
            ("rmp",),
            "rmp",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "source").mkdir(parents=True)
            prompt = root / "prompt.md"
            prompt.write_text("packet", encoding="utf-8")
            editor = RepairEditor()
            evaluator = ScopeEnforcingEvaluator(hypothesis, dict(self.parent.metrics))
            engine = GoalEvolveEngine(
                self.contract,
                root / "state",
                DiversePlanner(),
                evaluator,
                IsolatedWorkspace(),
                StrictEvidencePromotion(),
                student_editor=editor,
            )

            candidate = engine._edit_then_evaluate(
                self.parent, hypothesis, "student_1", workspace, prompt, 1
            )

            self.assertEqual(editor.repair_boundaries, [("src/rmp/src/Restructure.cpp",)])
            self.assertIn(
                "outside_assigned_patch_scope:src/rsz/src/policy/RepairPowerPolicy.cc",
                candidate.evaluation_error or "",
            )

    def test_constraint_repair_stays_within_initial_changed_files(self) -> None:
        class RepairEditor:
            name = "repair_editor"
            config = SimpleNamespace(max_repair_attempts=0)

            def __init__(self) -> None:
                self.repair_boundaries: list[tuple[str, ...]] = []

            def apply(self, **_: object) -> StudentEditReport:
                return StudentEditReport(True, "edited", "initial", "thread-1", {})

            def repair(self, *, hypothesis: Hypothesis, **_: object) -> StudentEditReport:
                self.repair_boundaries.append(hypothesis.allowed_patch_paths)
                return StudentEditReport(True, "repaired", "constraint", "thread-1", {})

        class ConstraintScopeEvaluator:
            name = "constraint_scope"

            def __init__(self, hypothesis: Hypothesis, parent_metrics: dict[str, float]) -> None:
                self.calls = 0
                self.hypothesis = hypothesis
                self.parent_metrics = parent_metrics

            def evaluate(self, *, student_id: str, hypothesis: Hypothesis, **_: object) -> CandidateResult:
                self.calls += 1
                checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
                diff = "+++ b/src/rmp/src/Restructure.cpp\n+RMP change\n"
                if self.calls == 2:
                    diff += "+++ b/src/rsz/src/policy/RepairPowerPolicy.cc\n+cross-mechanism repair\n"
                candidate = CandidateResult(
                    student_id,
                    hypothesis,
                    {**self.parent_metrics, "drv_count": 1.0},
                    {"accepted": 1.0},
                    checks,
                    diff,
                    f"commit-{self.calls}",
                )
                report = preflight_candidate(
                    candidate,
                    allowed_patch_paths=hypothesis.allowed_patch_paths,
                )
                if not report.ok:
                    candidate.evaluation_error = ";".join(report.violations)
                return candidate

        hypothesis = Hypothesis(
            "rmp",
            "rmp_area",
            "claim",
            ("src/rmp/src/Restructure.cpp",),
            ("accepted",),
            ("rmp",),
            "rmp",
        )
        parent = Parent(
            "baseline",
            {**self.parent.metrics, "drv_count": 0.0},
            "base",
            "hash",
            self.parent.goal_distance,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "source").mkdir(parents=True)
            prompt = root / "prompt.md"
            prompt.write_text("packet", encoding="utf-8")
            editor = RepairEditor()
            evaluator = ConstraintScopeEvaluator(hypothesis, dict(parent.metrics))
            engine = GoalEvolveEngine(
                self.contract,
                root / "state",
                DiversePlanner(),
                evaluator,
                IsolatedWorkspace(),
                StrictEvidencePromotion(),
                student_editor=editor,
            )

            candidate = engine._edit_then_evaluate(
                parent, hypothesis, "student_1", workspace, prompt, 1
            )

            self.assertEqual(editor.repair_boundaries, [("src/rmp/src/Restructure.cpp",)])
            self.assertIn(
                "outside_assigned_patch_scope:src/rsz/src/policy/RepairPowerPolicy.cc",
                candidate.evaluation_error or "",
            )

    def test_telemetry_repair_build_failure_returns_to_same_student(self) -> None:
        class TelemetryThenEngineeringEditor:
            name = "telemetry_then_engineering_editor"
            config = SimpleNamespace(max_repair_attempts=1)

            def __init__(self) -> None:
                self.repair_kinds: list[str] = []

            def apply(self, **_: object) -> StudentEditReport:
                return StudentEditReport(True, "edited", "initial", "thread-1", {})

            def repair(self, *, repair_kind: str, **_: object) -> StudentEditReport:
                self.repair_kinds.append(repair_kind)
                return StudentEditReport(
                    True,
                    "repaired",
                    repair_kind,
                    "thread-1",
                    {"repair_note": repair_kind},
                )

        class MissingTelemetryThenBuildFailure:
            name = "missing_telemetry_then_build_failure"

            def __init__(self, hypothesis: Hypothesis, build_log: Path) -> None:
                self.calls = 0
                self.hypothesis = hypothesis
                self.build_log = build_log

            def evaluate(self, *, student_id: str, **_: object) -> CandidateResult:
                self.calls += 1
                diff = "+++ b/src/rsz/src/RecoverPower.cc\n+policy\n"
                if self.calls == 2:
                    return CandidateResult(
                        student_id,
                        self.hypothesis,
                        {},
                        {},
                        [],
                        diff,
                        "telemetry-broken",
                        artifacts={"build_log": str(self.build_log)},
                        evaluation_error="RuntimeError:candidate_build_failed:1",
                    )
                checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
                return CandidateResult(
                    student_id,
                    self.hypothesis,
                    dict(self.parent_metrics),
                    {} if self.calls == 1 else {"accepted": 1.0},
                    checks,
                    diff,
                    f"commit-{self.calls}",
                )

            parent_metrics: dict[str, float] = {}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "source").mkdir(parents=True)
            prompt = root / "prompt.md"
            prompt.write_text("packet", encoding="utf-8")
            build_log = root / "build.log"
            build_log.write_text("undefined reference to old ABI\n", encoding="utf-8")
            editor = TelemetryThenEngineeringEditor()
            evaluator = MissingTelemetryThenBuildFailure(self.hypothesis, build_log)
            evaluator.parent_metrics = dict(self.parent.metrics)
            engine = GoalEvolveEngine(
                self.contract,
                root / "state",
                DiversePlanner(),
                evaluator,
                IsolatedWorkspace(),
                StrictEvidencePromotion(),
                student_editor=editor,
            )

            candidate = engine._edit_then_evaluate(
                self.parent, self.hypothesis, "student_1", workspace, prompt, 1
            )

            self.assertEqual(evaluator.calls, 3)
            self.assertEqual(editor.repair_kinds, ["telemetry", "telemetry_engineering"])
            self.assertIsNone(candidate.evaluation_error)
            self.assertEqual(candidate.phase_signals, {"accepted": 1.0})
            self.assertEqual(
                candidate.artifacts["telemetry_engineering_repair_repair_note"],
                "telemetry_engineering",
            )
            self.assertTrue(
                (
                    workspace.parent
                    / "artifacts"
                    / "repair_attempts"
                    / "telemetry_engineering"
                    / "attempt_01"
                    / "evaluation"
                    / "candidate_before_repair.json"
                ).is_file()
            )

    def test_repair_snapshot_ignores_long_structured_codex_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "source").mkdir(parents=True)
            build_log = root / "build.log"
            build_log.write_text("compiler error\n", encoding="utf-8")
            candidate = CandidateResult(
                "student_1",
                self.hypothesis,
                {},
                {},
                [],
                "+++ b/src/rsz/src/RecoverPower.cc\n+change\n",
                "commit",
                artifacts={"build_log": str(build_log), "codex": "{" + "x" * 4096 + "}"},
                evaluation_error="candidate_build_failed",
            )
            GoalEvolveEngine._snapshot_failed_evaluation(candidate=candidate, workspace=workspace, repair_attempt=1)
            retained = workspace.parent / "artifacts" / "repair_attempts" / "engineering" / "attempt_01" / "evaluation"
            self.assertTrue((retained / "build_log.log").is_file())
            self.assertTrue((retained / "candidate_before_repair.json").is_file())

    def test_baseline_epd_checkpoint_feeds_parent_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            checkpoint = Path(temporary) / "baseline_checkpoints.json"
            atomic_json(checkpoint, {"checkpoints": {"post_route": {"tns_abs_ns": 130.0}}})
            db = EvolutionProgramDatabase(root)
            db.ensure_baseline(self.parent)
            db.attach_baseline_evaluation(
                parent=self.parent,
                metrics=dict(self.parent.metrics),
                goal_distance=self.parent.goal_distance,
                artifacts={"checkpoint_metrics": str(checkpoint)},
                passed=True,
            )
            engine = GoalEvolveEngine(self.contract, root, DiversePlanner(), MockEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion())
            self.assertEqual(engine._parent_checkpoints(self.parent), {"post_route": {"tns_abs_ns": 130.0}})

    def test_configured_baseline_requires_contract_consistency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state, artifact = root / "state", root / "baseline"
            artifact.mkdir()
            engine = GoalEvolveEngine(self.contract, state, DiversePlanner(), MockEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion())
            engine.initialize(baseline_metrics=dict(self.contract.baseline_metrics))
            atomic_json(
                artifact / "baseline.json",
                {
                    "ok": True,
                    "metrics": dict(self.contract.baseline_metrics),
                    "artifacts": {"checkpoint_metrics": str(artifact / "checkpoints.json")},
                },
            )
            atomic_json(artifact / "checkpoints.json", {"checkpoints": {"post_route": {"tns_abs_ns": 120.0}}})
            _attach_configured_baseline(engine=engine, config=SimpleNamespace(baseline_evaluation_root=artifact))
            summary = EvolutionProgramDatabase(state).summary()
            self.assertEqual(summary["status_counts"]["validated"], 0)
            self.assertEqual(summary["baseline_record_count"], 1)

    def test_observation_memory_is_source_scoped_and_compact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = CandidateResult(
                "student_1", self.hypothesis, {"tns_abs_ns": 90.0}, {"accepted": 2.0}, [], "+++ b/src/rsz/src/RecoverPower.cc\n+change\n", "commit"
            )
            verdict = classify_candidate(contract=self.contract, parent=self.parent, candidate=candidate)
            memory = ObservationMemory(Path(temporary))
            memory.record(round_index=1, candidate=candidate, verdict=verdict)
            summary = memory.summary()
            self.assertEqual(summary["observation_count"], 1)
            family = summary["family_hook_summary"][0]
            self.assertEqual(family["mechanism_family"], "ranking")
            self.assertEqual(family["source_hooks"], ["src/rsz/src/RecoverPower.cc"])

    def test_official_four_of_four_checker_accepts_baseline(self) -> None:
        benchmark_root = DEFAULT_BENCHMARK_ROOT
        baseline = benchmark_root / "aes_cipher_top"
        if not baseline.is_dir():
            self.skipTest("reference benchmark data is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            passed, detail = official_four_check(
                pre_opt=baseline,
                post_opt=baseline,
                output_log=Path(temporary) / "official_4of4.log",
                policy=ExecutionPolicy(timeout_s=60, retries=0, min_free_gb=0),
            )
        self.assertTrue(passed, detail)

    def test_scope_resolver_verifies_existing_source_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src/rsz/src"
            source.mkdir(parents=True)
            (source / "MoveCommitter.cc").write_text('void commit() { logger("METRIC|accepted_commit|1"); }\n', encoding="utf-8")
            planner = DiversePlanner(scope_resolver=SourceScopeResolver(root))
            plans = planner.plan(contract=self.contract, parent=self.parent, round_index=1, student_ids=("student_1",), state_root=root / "state")
            self.assertEqual(plans[0].source_hooks, ("src/rsz/src/MoveCommitter.cc",))
            self.assertIn("scope_confidence:verified_hook", plans[0].scope_evidence)

    def test_route_debt_retrieval_uses_unsuppressed_route_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            (root / "knowledge").mkdir(parents=True)
            atomic_json(
                root / "knowledge" / "feedback.json",
                {
                    "suppressed_card_ids": [
                        "drv_repair_gate",
                        "path_frontier_selection",
                        "power_candidate_ranking",
                        "runtime_budget_guard",
                        "timing_commit_guard",
                    ]
                },
            )
            planner = DiversePlanner()
            cards = planner.retriever.retrieve(
                parent=self.parent,
                symptoms=("tns", "route", "post_route"),
                state_root=root,
                count=4,
            )
            self.assertEqual(len(cards), 4)
            self.assertIn("route_sta_query_activation", {card.card_id for card in cards})
            plans = planner.plan(
                contract=self.contract,
                parent=self.parent,
                round_index=1,
                student_ids=("student_1", "student_2", "student_3", "student_4"),
                state_root=root,
                diagnosis=SimpleNamespace(responsible_stage="post_route"),
            )
            self.assertTrue(all(item.source_hooks[0].startswith("src/grt/") for item in plans))
            self.assertFalse((root / "knowledge" / "retrieval_ledger.json").exists())
            planner.commit_round(
                contract=self.contract,
                parent=self.parent,
                round_index=1,
                state_root=root,
                diagnosis=SimpleNamespace(responsible_stage="post_route"),
                hypotheses=plans,
            )
            committed = load_json(root / "knowledge" / "retrieval_ledger.json", {})
            self.assertEqual(len(committed.get("rounds", [])), 1)

    def test_runtime_only_card_is_excluded_without_runtime_symptom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cards = DiversePlanner().retriever.retrieve(
                parent=self.parent,
                symptoms=("leakage", "dynamic"),
                state_root=Path(temporary),
                count=12,
            )
            self.assertNotIn("runtime_budget_guard", {card.card_id for card in cards})

    def test_power_reclaim_stage_constrains_retrieval_before_teacher_planning(self) -> None:
        cards = (
            MechanismCard("timing", "timing", ("tns", "timing"), ("src/rsz/src/Timing.cc",), ("timing",), "timing"),
            MechanismCard("power", "power", ("leakage", "dynamic", "power"), ("src/rsz/src/policy/RepairPowerPolicy.cc",), ("power",), "power", power_command_eligible=True),
        )
        with tempfile.TemporaryDirectory() as temporary:
            plan = DiversePlanner(DiverseRetriever(cards)).plan(
                contract=self.contract,
                parent=self.parent,
                round_index=1,
                student_ids=("student_1",),
                state_root=Path(temporary),
                decision_context={
                    "stage": "power_reclaim",
                    "source_focus": ["src/rsz/src/policy/RepairPowerPolicy.cc"],
                },
            )
        self.assertEqual(plan[0].retrieval_ids, ("power",))
        self.assertEqual(plan[0].allowed_patch_paths, ("src/rsz/src/policy/RepairPowerPolicy.cc",))

    def test_diagnostic_only_card_is_excluded_from_qor_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cards = DiversePlanner().retriever.retrieve(
                parent=self.parent,
                symptoms=("leakage", "dynamic"),
                state_root=Path(temporary),
                count=12,
            )
            self.assertNotIn("optimizer_phase_power_attribution", {card.card_id for card in cards})

    def test_retrieval_never_revives_hard_suppressed_cards_to_fill_batch(self) -> None:
        cards = (
            MechanismCard("a", "a", ("leakage",), ("src/rsz/src/A.cc",), ("a",), "a"),
            MechanismCard("b", "b", ("leakage",), ("src/rsz/src/B.cc",), ("b",), "b"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "knowledge").mkdir()
            atomic_json(root / "knowledge" / "feedback.json", {"suppressed_card_ids": ["a"]})
            result = DiverseRetriever(cards).retrieve(
                parent=self.parent,
                symptoms=("leakage",),
                state_root=root,
                count=2,
            )
        self.assertEqual(["b"], [card.card_id for card in result])

    def test_unactivated_refutation_is_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "knowledge").mkdir()
            engine = GoalEvolveEngine(
                self.contract,
                root,
                SimpleNamespace(name="planner"),
                SimpleNamespace(name="evaluator"),
                SimpleNamespace(name="workspace"),
                StrictEvidencePromotion(),
            )
            hypothesis = Hypothesis(
                "activation", "activation", "claim", ("src/rsz/src/A.cc",), ("activated",), ("activation_card",), "timing"
            )
            candidate = CandidateResult(
                "student", hypothesis,
                {"tns_abs_ns": 100.0, "leakage_power_pw": 200.0}, {},
                [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                "+++ b/src/rsz/src/A.cc\n+change\n", "commit",
            )
            verdict = engine.promotion_policy.classify(contract=self.contract, parent=self.parent, candidate=candidate)
            self.assertEqual(verdict.state, "refuted")
            self.assertFalse(verdict.mechanism_fired)
            engine._record_feedback(round_index=1, rows=({"candidate": candidate, "verdict": verdict},))
            feedback = load_json(root / "knowledge" / "feedback.json")
            self.assertNotIn("activation_card", feedback["suppressed_card_ids"])
            self.assertIn("activation_card", feedback["activation_retry_card_ids"])

    def test_conclusive_nonactivation_log_suppresses_duplicate_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "knowledge").mkdir()
            evaluation_log = root / "evaluation.log"
            evaluation_log.write_text("RMP_GUARD|sta_select_no_accept|candidates=4\n", encoding="utf-8")
            engine = GoalEvolveEngine(
                self.contract,
                root,
                SimpleNamespace(name="planner"),
                SimpleNamespace(name="evaluator"),
                SimpleNamespace(name="workspace"),
                StrictEvidencePromotion(),
            )
            hypothesis = Hypothesis(
                "rmp", "rmp", "claim", ("src/rmp/src/Restructure.cpp",),
                ("rmp_examined",), ("rmp_card",), "rmp",
                conclusive_nonactivation_patterns=("RMP_GUARD|sta_select_no_accept",),
            )
            candidate = CandidateResult(
                "student", hypothesis,
                {"tns_abs_ns": 100.0, "leakage_power_pw": 200.0}, {},
                [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                "+++ b/src/rmp/src/Restructure.cpp\n+change\n", "commit",
                artifacts={"evaluation_log": str(evaluation_log)},
            )
            verdict = engine.promotion_policy.classify(contract=self.contract, parent=self.parent, candidate=candidate)
            self.assertFalse(verdict.mechanism_fired)
            engine._record_feedback(round_index=1, rows=({"candidate": candidate, "verdict": verdict},))
            feedback = load_json(root / "knowledge" / "feedback.json")
            self.assertIn("rmp_card", feedback["suppressed_card_ids"])
            self.assertNotIn("rmp_card", feedback["activation_retry_card_ids"])

    def test_conclusive_teacher_suppression_closes_an_exhausted_activation_family(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "knowledge").mkdir()
            engine = GoalEvolveEngine(
                self.contract,
                root,
                SimpleNamespace(name="planner"),
                SimpleNamespace(name="evaluator"),
                SimpleNamespace(name="workspace"),
                StrictEvidencePromotion(),
            )
            hypothesis = Hypothesis(
                "reroute", "timing_reroute", "claim", ("src/rsz/src/A.cc",),
                ("reroute_examined",), ("reroute_card",), "reroute"
            )
            candidate = CandidateResult(
                "student", hypothesis,
                {"tns_abs_ns": 100.0, "leakage_power_pw": 200.0}, {},
                [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                "+++ b/src/rsz/src/A.cc\n+change\n", "commit",
            )
            atomic_json(
                root / "knowledge" / "feedback.json",
                {"suppressed_card_ids": [], "activation_retry_card_ids": ["reroute_card"]},
            )
            engine._apply_teacher_review_feedback(
                round_index=7,
                review={"mechanism_actions": [{
                    "mechanism_family": "timing_reroute",
                    "action": "suppress",
                    "evidence_classification": "activation_failure_engineering_exhausted",
                }]},
                rows=({"candidate": candidate},),
            )
            feedback = load_json(root / "knowledge" / "feedback.json")
            self.assertIn("reroute_card", feedback["suppressed_card_ids"])
            self.assertNotIn("reroute_card", feedback["activation_retry_card_ids"])
            self.assertEqual("timing_reroute", feedback["teacher_suppression_history"][-1]["mechanism_family"])

    def test_retrieval_uses_new_mechanisms_after_prior_cards_are_suppressed(self) -> None:
        cards = (
            MechanismCard("old", "old", ("leakage",), ("src/rsz/src/Old.cc",), ("old",), "old"),
            MechanismCard("new", "new", ("leakage", "dynamic"), ("src/rsz/src/New.cc",), ("new",), "new"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "knowledge").mkdir()
            atomic_json(root / "knowledge" / "feedback.json", {"suppressed_card_ids": ["old"]})
            result = DiverseRetriever(cards).retrieve(
                parent=self.parent,
                symptoms=("leakage", "dynamic"),
                state_root=root,
                count=4,
            )
        self.assertEqual(["new"], [card.card_id for card in result])

    def test_retrieval_excludes_card_already_integrated_in_current_parent(self) -> None:
        cards = (
            MechanismCard("integrated", "guard", ("leakage",), ("src/rsz/src/A.cc",), ("a",), "a"),
            MechanismCard("fresh", "fresh", ("leakage",), ("src/rsz/src/B.cc",), ("b",), "b"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hypothesis = root / "rounds" / "round_014" / "students" / "student_1" / "artifacts" / "hypothesis.json"
            hypothesis.parent.mkdir(parents=True)
            atomic_json(hypothesis, {"retrieval_ids": ["integrated"]})
            parent = Parent("round_014:student_1", self.parent.metrics, "commit", "hash", self.parent.goal_distance)
            result = DiverseRetriever(cards).retrieve(
                parent=parent,
                symptoms=("leakage",),
                state_root=root,
                count=2,
            )
        self.assertEqual(["fresh"], [card.card_id for card in result])

    def test_broad_power_search_uses_source_disjoint_first_pass(self) -> None:
        """Different card names must not spend a batch on the same C++ hook."""
        with tempfile.TemporaryDirectory() as temporary:
            cards = DiversePlanner().retriever.retrieve(
                parent=self.parent,
                symptoms=("leakage", "dynamic"),
                state_root=Path(temporary),
                count=4,
            )
        used: set[str] = set()
        for card in cards:
            self.assertFalse(used.intersection(card.source_hooks), card.card_id)
            used.update(card.source_hooks)

    def test_timing_debt_recovery_cards_outrank_generic_timing_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cards = DiversePlanner().retriever.retrieve(
                parent=self.parent,
                symptoms=("tns", "timing_recovery"),
                state_root=Path(temporary),
                count=4,
            )
            self.assertEqual(
                {
                    "timing_recovery_rmp_delay_sta_bracket",
                    "timing_recovery_rmp_path_cone_v4",
                    "timing_recovery_rmp_path_cone_halo_v5",
                    "timing_recovery_rmp_global_tns_wire_guard",
                },
                {card.card_id for card in cards},
            )

    def test_completed_flow_signal_suppresses_redundant_activation_card(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "rounds" / "round_001" / "students" / "student_1" / "artifacts" / "contest_output" / "evaluation.log"
            log.parent.mkdir(parents=True)
            (log.parents[4] / "round.json").write_text("{}\n", encoding="utf-8")
            log.write_text("METRIC|resistance_aware_nets|42\n", encoding="utf-8")
            cards = DiversePlanner().retriever.retrieve(
                parent=self.parent,
                symptoms=("leakage", "dynamic"),
                state_root=root,
                count=12,
            )
            self.assertNotIn("route_resistance_aware_topology", {card.card_id for card in cards})

    def test_activated_tradeoff_prioritizes_its_bounded_repair_card(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "rounds" / "round_001" / "students" / "student_1" / "artifacts" / "contest_output" / "evaluation.log"
            log.parent.mkdir(parents=True)
            (log.parents[4] / "round.json").write_text("{}\n", encoding="utf-8")
            log.write_text(
                "METRIC|repair_power_committed|12\n"
                "METRIC|repair_power_leakage_gain|1.0\n"
                "METRIC|repair_power_timing_rejected|3\n"
                "METRIC|repair_power_timing_retained|1\n",
                encoding="utf-8",
            )
            cards = DiversePlanner().retriever.retrieve(
                parent=self.parent,
                symptoms=("leakage", "dynamic"),
                state_root=root,
                count=1,
            )
        self.assertEqual(cards[0].card_id, "repair_power_explicit_timing_budget")


if __name__ == "__main__":
    unittest.main()
