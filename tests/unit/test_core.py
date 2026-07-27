from __future__ import annotations

import inspect
import json
import os
import tempfile
import unittest
import shutil
from pathlib import Path
from types import SimpleNamespace

from goalevolve.config import DEFAULT_CREDENTIAL_ENV, DEFAULT_OPENROAD_SEED, load_config
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
from goalevolve.core.models import CandidateResult, CheckResult, Hypothesis, Parent
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
            target_ratios={"tns_abs_ns": 0.5, "leakage_power_pw": 0.8},
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
            target_ratios={
                "tns_abs_ns": 0.5,
                "runtime_s": 0.5,
                "SPPA": 2.0,
                "Sfinal": 2.0,
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
                target_ratios={"tns_abs_ns": 0.8, "leakage_power_pw": 0.9},
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
                target_ratios={"tns_abs_ns": 0.5, "leakage_power_pw": 0.7},
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
            target_ratios={"tns_abs_ns": 12.0 / 8.89, "dynamic_power_pw": 350.0 / 381.9318, "leakage_power_pw": 35.0 / 68.2},
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
            target_ratios={"tns_abs_ns": 12.0 / 8.89, "dynamic_power_pw": 350.0 / 381.9318, "leakage_power_pw": 35.0 / 68.2},
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
            target_ratios={"tns_abs_ns": 12.0 / 8.89, "dynamic_power_pw": 350.0 / 381.9318, "leakage_power_pw": 35.0 / 68.2},
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
            target_ratios={
                "tns_abs_ns": 53.0 / 48.77,
                "dynamic_power_pw": 250_000_000_000.0 / 293_826_000_000.0,
                "leakage_power_pw": 80_000_000.0 / 174_000_000.0,
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
        self.assertEqual(adaptive["adaptive_allocation"]["upstream_power_slot"], 1)

    def test_adaptive_tradeoff_preserves_satisfied_power_target(self) -> None:
        contract = build_contract(
            design="adaptive",
            baseline_metrics={"tns_abs_ns": 100.0, "dynamic_power_pw": 300.0, "leakage_power_pw": 180.0},
            target_ratios={"tns_abs_ns": 0.5, "dynamic_power_pw": 250.0 / 300.0, "leakage_power_pw": 80.0 / 180.0},
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
            target_ratios={
                "tns_abs_ns": 53.0 / 48.77,
                "dynamic_power_pw": 250_000_000_000.0 / 293_826_000_000.0,
                "leakage_power_pw": 80_000_000.0 / 174_000_000.0,
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
            for index in range(1, 4):
                round_data = load_json(root / "rounds" / f"round_{index:03d}" / "round.json")
                self.assertEqual(round_data["common_parent_id_at_start"], previous_parent)
                self.assertEqual(round_data["student_count"], 4)
                self.assertEqual(round_data["promoted_student"], "student_1")
                self.assertEqual(len({row["hypothesis_id"] for row in round_data["results"]}), 4)
                previous_parent = f"round_{index:03d}:student_1"
            ledger = load_json(root / "knowledge" / "retrieval_ledger.json")
            self.assertEqual(len(ledger["rounds"]), 3)
            self.assertTrue(all(len(row["card_ids"]) == 4 for row in ledger["rounds"]))
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
            benchmark_root = Path("/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/benchmarks")
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
                "target_ratios": {"tns_abs_ns": 1.0},
                "evaluator": "contest_openroad",
            })
            self.assertEqual(load_config(path).source_root, DEFAULT_OPENROAD_SEED)
            self.assertTrue(DEFAULT_OPENROAD_SEED.is_dir())

    def test_portable_contest_profiles_resolve_only_project_assets(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        expected_designs = {
            "ariane",
            "jpeg_encoder",
            "mempool_group",
            "nvdla_a",
            "nvdla_c",
            "nvdla_m",
            "nvdla_p",
        }
        profiles = sorted((project_root / "experiments" / "contest2026").glob("*.bootstrap.json"))
        self.assertEqual({path.name.removesuffix(".bootstrap.json") for path in profiles}, expected_designs)
        for profile in profiles:
            config = load_config(profile)
            self.assertEqual(config.credential_env, DEFAULT_CREDENTIAL_ENV)
            self.assertTrue(config.source_root and config.source_root.is_dir())
            self.assertTrue((config.source_root / "CMakeLists.txt").is_file())
            benchmark = config.benchmark_root / config.design
            self.assertTrue((benchmark / f"{config.design}.def.gz").is_file())
            self.assertTrue((benchmark / f"{config.design}.v").is_file())
            self.assertTrue((benchmark / f"{config.design}.sdc").is_file())
            self.assertTrue((benchmark / "metrics.csv").is_file())

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

    def test_checkpoint_parser_and_epd_four_statuses(self) -> None:
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
            checks = [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")]
            cases = [
                ("validated", CandidateResult("s", self.hypothesis, {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0}, {"accepted": 1.0}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+x\n", "v")),
                ("promising", CandidateResult("s", self.hypothesis, {"tns_abs_ns": 60.0, "leakage_power_pw": 170.0}, {}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+y\n", "p")),
                ("pending", CandidateResult("s", self.hypothesis, {}, {}, [], "+++ b/src/rsz/src/RecoverPower.cc\n+z\n", "q", evaluation_error="candidate_build_failed")),
                ("invalid", CandidateResult("s", self.hypothesis, {"tns_abs_ns": 110.0, "leakage_power_pw": 220.0}, {"accepted": 1.0}, checks, "+++ b/src/rsz/src/RecoverPower.cc\n+w\n", "i")),
            ]
            for _, candidate in cases:
                verdict = classify_candidate(contract=self.contract, parent=self.parent, candidate=candidate)
                db.record(round_index=1, parent=self.parent, candidate=candidate, verdict=verdict)
            statuses = {row["epd_status"] for row in db.records()}
            self.assertTrue(set(EPD_STATUSES).issubset(statuses))
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
        benchmark_root = Path("/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks/benchmarks")
        baseline = benchmark_root / "aes_cipher_top"
        if not baseline.is_dir():
            self.skipTest("MLCAD 2026 benchmark data is not installed")
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
