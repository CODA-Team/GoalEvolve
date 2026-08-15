from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from goalevolve.core.contracts import build_contract
from goalevolve.core.io import atomic_json, load_json
from goalevolve.core.models import Parent
from goalevolve.evaluation.promotion import StrictEvidencePromotion
from goalevolve.execution.engine import GoalEvolveEngine
from goalevolve.planning.parent_selection import (
    ParentPortfolioEntry,
    ParentSelectionPolicy,
)
from goalevolve.planning.retrieval import DiversePlanner
from goalevolve.testing.evaluators import MockEvaluator
from goalevolve.execution.workspace import IsolatedWorkspace


class ObjectiveEquationTests(unittest.TestCase):
    def test_goal_contract_uses_paper_normalization_scale_and_weighted_sum(self) -> None:
        """Removing the paper scale or sum must change this observable distance."""
        contract = build_contract(
            design="equations",
            baseline_metrics={"tns_abs_ns": 10.0, "dynamic_power_pw": 20.0},
            target_metrics={"tns_abs_ns": 4.0, "dynamic_power_pw": 10.0},
            weights={"tns_abs_ns": 1.0, "dynamic_power_pw": 2.0},
        )

        distance, residuals, missing = contract.evaluate(
            {"tns_abs_ns": 7.0, "dynamic_power_pw": 15.0}
        )

        self.assertEqual([], missing)
        self.assertEqual({"tns_abs_ns": 0.3, "dynamic_power_pw": 0.25}, residuals)
        self.assertEqual(0.8, distance)

    def test_parent_selection_applies_distance_diversity_and_tag_alignment(self) -> None:
        """A timing bottleneck receives the documented lower power compatibility."""
        champion = Parent("champion", {}, "c0", "h0", 0.10)
        power = Parent("power", {}, "c1", "h1", 0.20)
        selector = ParentSelectionPolicy()

        probabilities = selector.probabilities(
            portfolio=(
                ParentPortfolioEntry(champion, "timing"),
                ParentPortfolioEntry(power, "power"),
            ),
            bottleneck_tag="timing",
            selection_history=("timing",),
            plateau_iteration=0,
        )

        champion_score = 1.0 * (1.0 + 0.25 * 0.9) * (1.0 + 0.50 * 1.00)
        power_score = math.exp(-(0.20 - 0.10) / 0.75) * (1.0 + 0.25 * 1.0) * (
            1.0 + 0.50 * 0.30
        )
        expected_power_probability = power_score / (champion_score + power_score)
        self.assertAlmostEqual(expected_power_probability, probabilities["power"], places=12)

    def test_engine_activates_parent_sampling_only_after_the_configured_plateau(self) -> None:
        """Removing the plateau gate would make the first selection record stochastic."""
        contract = build_contract(
            design="plateau",
            baseline_metrics={"tns_abs_ns": 10.0},
            target_metrics={"tns_abs_ns": 5.0},
        )
        champion = Parent("champion", {"tns_abs_ns": 6.0}, "c0", "h0", 0.2)
        alternate = Parent("alternate", {"tns_abs_ns": 7.0}, "c1", "h1", 0.4)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for entry in (champion, alternate):
                (root / "parents" / entry.source_hash / "source").mkdir(parents=True)
            atomic_json(root / "rounds" / "round_001" / "round.json", {
                "parent_after": champion.to_dict(),
                "promoted_mechanism_family": "timing",
            })
            atomic_json(root / "rounds" / "round_002" / "round.json", {
                "parent_after": alternate.to_dict(),
                "promoted_mechanism_family": "power",
            })
            engine = GoalEvolveEngine(
                contract, root, DiversePlanner(), MockEvaluator(), IsolatedWorkspace(), StrictEvidencePromotion()
            )

            self.assertEqual(champion, engine._select_plateau_parent(round_index=3, parent=alternate))
            self.assertEqual(champion, engine._select_plateau_parent(round_index=4, parent=champion))
            self.assertEqual(champion, engine._select_plateau_parent(round_index=5, parent=champion))
            selected = engine._select_plateau_parent(round_index=6, parent=champion)

            self.assertIn(selected.parent_id, {"champion", "alternate"})
            record = load_json(root / "rounds" / "round_006" / "parent_selection.json")
            self.assertTrue(record["plateau_active"])
            self.assertEqual(3, record["rounds_since_improvement"])
            self.assertEqual(
                {"timing", "power"},
                {entry["tag"] for entry in record["portfolio"]},
            )


if __name__ == "__main__":
    unittest.main()
