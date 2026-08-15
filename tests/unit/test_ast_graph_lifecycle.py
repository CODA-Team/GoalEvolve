from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from goalevolve.core.contracts import build_contract
from goalevolve.core.models import Parent
from goalevolve.execution.engine import GoalEvolveEngine
from goalevolve.execution.workspace import IsolatedWorkspace
from goalevolve.evaluation.promotion import StrictEvidencePromotion
from goalevolve.planning.retrieval import DiversePlanner


class _StaticEvaluator:
    name = "static"
    config = SimpleNamespace(allowed_patch_roots=("src/rsz",))


class AstGraphLifecycleTests(unittest.TestCase):
    def test_promoted_parent_refresh_is_incremental_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "seed"
            first = source / "src/rsz/src/First.cc"
            second = source / "src/rsz/src/Second.cc"
            first.parent.mkdir(parents=True)
            first.write_text("namespace rsz { int first() { return 1; } }\n", encoding="utf-8")
            second.write_text("namespace rsz { int second() { return 2; } }\n", encoding="utf-8")
            contract = build_contract(
                design="ast_lifecycle",
                baseline_metrics={"tns_abs_ns": 10.0},
                target_metrics={"tns_abs_ns": 1.0},
            )
            state = root / "campaign"
            engine = GoalEvolveEngine(
                contract,
                state,
                DiversePlanner(),
                _StaticEvaluator(),
                IsolatedWorkspace(source),
                StrictEvidencePromotion(),
            )
            baseline = Parent("baseline", {"tns_abs_ns": 10.0}, "base", "baseline", 1.0)
            engine.workspace_provider.ensure_parent(state_root=state, parent=baseline)
            engine._refresh_promoted_parent_repository_graph(parent=baseline)

            promoted = Parent("round_001:student_1", {"tns_abs_ns": 5.0}, "child", "child_hash", 0.5)
            promoted_source = state / "parents" / promoted.source_hash / "source"
            shutil.copytree(state / "parents" / baseline.source_hash / "source", promoted_source)
            (promoted_source / "src/rsz/src/Second.cc").write_text(
                "namespace rsz { int second() { return 3; } }\n", encoding="utf-8"
            )

            audit = engine._refresh_promoted_parent_repository_graph(parent=promoted)
            self.assertEqual(audit["status"], "ready")
            self.assertEqual(audit["reparsed_files"], ["src/rsz/src/Second.cc"])
            self.assertEqual(audit["reused_files"], ["src/rsz/src/First.cc"])
            self.assertTrue(
                (state / "parents" / promoted.source_hash / "repository_graph_audit.json").is_file()
            )
