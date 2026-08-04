from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from goalevolve.core.io import atomic_json, load_json
from goalevolve.core.models import CandidateResult, CheckResult, EvidenceVerdict, Hypothesis, Parent
from goalevolve.planning.epd import EvolutionProgramDatabase


class EPDProjectionTests(unittest.TestCase):
    def test_projection_materializes_path_addressable_idea_attempt_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = Parent(
                "baseline",
                {"tns_abs_ns": 12.0, "dynamic_power_pw": 400.0, "leakage_power_pw": 90.0},
                "base-commit",
                "base-hash",
                0.5,
            )
            reflection = root / "student_reflection.md"
            reflection.write_text("The bounded guard fired and retained one legal move.\n", encoding="utf-8")
            checkpoints = root / "checkpoint_metrics.json"
            atomic_json(checkpoints, {"checkpoints": {"post_repair_power": {"tns_abs_ns": 11.0}}})
            epd = EvolutionProgramDatabase(root)
            idea_id = epd.register_teacher_ideas(
                round_index=3,
                parent=parent,
                evolution_ideas=(
                    {
                        "idea": "Reject a late candidate whose observed slack would consume timing reserve.",
                        "source_hooks": ("src/rsz/src/policy/RepairPowerPolicy.cc",),
                        "expected_signals": ("late_candidate_rejected",),
                        "observed_state": "candidate slack and VT rank",
                        "decision_boundary": "late candidate admission",
                        "proposed_action": "reject unsafe VT transitions",
                        "acceptance_or_rollback_rule": "retain only when timing reserve remains",
                    },
                ),
            )[0]
            hypothesis = Hypothesis(
                "r003_student_1_late_guard",
                "late_candidate_guard",
                "Reject a late candidate whose observed slack would consume timing reserve.",
                ("src/rsz/src/policy/RepairPowerPolicy.cc",),
                ("late_candidate_rejected",),
                ("late_guard",),
                "late_candidate_guard",
                epd_idea_id=idea_id,
            )
            candidate = CandidateResult(
                "student_1",
                hypothesis,
                {"tns_abs_ns": 11.0, "dynamic_power_pw": 390.0, "leakage_power_pw": 80.0},
                {"late_candidate_rejected": 2.0},
                [CheckResult(name, True) for name in ("build", "flow", "metrics", "lec")],
                "+++ b/src/rsz/src/policy/RepairPowerPolicy.cc\n+if (unsafe) return;\n",
                "candidate-commit",
                artifacts={
                    "checkpoint_metrics": str(checkpoints),
                    "student_reflection": str(reflection),
                },
            )
            record = epd.record(
                round_index=3,
                parent=parent,
                candidate=candidate,
                verdict=EvidenceVerdict("validated", 0.25, 0.25, True, True, ("verified",)),
            )

            epd_root = root / "knowledge" / "epd"
            manifest = load_json(epd_root / "manifest.json")
            idea = load_json(epd_root / "ideas" / idea_id / "idea.json")
            attempt_root = epd_root / "attempts" / record.record_id
            explorer_view = load_json(epd_root / "round_views" / "round_003" / "explorer_view.json")
            catalog_before = (epd_root / "indexes" / "idea_catalog.jsonl").read_text(encoding="utf-8")
            epd.rebuild_projection()
            catalog_after = (epd_root / "indexes" / "idea_catalog.jsonl").read_text(encoding="utf-8")

            self.assertEqual(manifest["compatibility_epd"], str(root / "knowledge" / "epd.json"))
            self.assertEqual(idea["observed_state"], "candidate slack and VT rank")
            self.assertEqual(idea["decision_boundary"], "late candidate admission")
            self.assertTrue((attempt_root / "attempt.json").is_file())
            self.assertTrue((attempt_root / "stage_metrics.json").is_file())
            self.assertTrue((attempt_root / "phase_signals.json").is_file())
            self.assertTrue((attempt_root / "student_reflection.md").is_file())
            self.assertTrue((attempt_root / "implementation.diff").is_file())
            self.assertTrue((attempt_root / "evidence_manifest.json").is_file())
            self.assertTrue((epd_root / "indexes" / "retrieval_corpus.jsonl").is_file())
            self.assertEqual(explorer_view["idea_catalog"], str(epd_root / "indexes" / "idea_catalog.jsonl"))
            self.assertEqual([row["idea_id"] for row in explorer_view["ideas"]], [idea_id])
            self.assertEqual(catalog_before, catalog_after)


if __name__ == "__main__":
    unittest.main()
