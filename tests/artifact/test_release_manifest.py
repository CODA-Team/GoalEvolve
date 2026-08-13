from __future__ import annotations

import tempfile
import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from artifact_evaluation import runner
from artifact_evaluation import import_v2_ae2_records as release_import
from artifact_evaluation.runner import _artifact, _artifacts, ae1
from goalevolve.core.io import sha256_file
from goalevolve.planning.repository_graph import RepositoryGraphIndex


class ReleaseArtifactTests(unittest.TestCase):
    def test_checked_in_p0_repository_graph_matches_the_frozen_source(self) -> None:
        source_root = runner.PROJECT_ROOT / "artifact_evaluation/lineage/openroad_power/p0/source"
        checked_in_root = source_root.parent / "repository_graph"
        with tempfile.TemporaryDirectory() as directory:
            regenerated_root = Path(directory) / "repository_graph"
            graph = RepositoryGraphIndex(
                state_root=Path(directory) / "state",
                p0_source_root=source_root,
                p0_artifact_root=regenerated_root,
            ).build_p0()
            for filename in ("manifest.json", "graph.json", "doc_cards.json"):
                self.assertTrue((graph.artifact_root / filename).is_file())
                self.assertEqual(
                    (graph.artifact_root / filename).read_bytes(),
                    (checked_in_root / filename).read_bytes(),
                    filename,
                )
        manifest = json.loads(
            (source_root.parent / "source_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(graph.source_hash, manifest["content_sha256"])
        self.assertEqual(graph.base_source_hash, manifest["content_sha256"])
        self.assertEqual(graph.allowed_patch_roots, ("src/rmp", "src/rsz"))
        self.assertTrue(graph.files)
        for path, source_file in graph.files.items():
            self.assertTrue((source_root / path).is_file(), path)
            self.assertEqual(source_file.digest, sha256_file(source_root / path), path)
            self.assertNotIn("/test/", path)
            self.assertNotIn("/tests/", path)

    def test_snapshot_verification_honors_manifest_capture_excludes(self) -> None:
        manifest = runner.PROJECT_ROOT / "artifact_evaluation/lineage/openroad_power/p0/source_manifest.json"
        source = manifest.parent / "source"

        self.assertTrue(runner._snapshot_matches(source=source, manifest_path=manifest))

    def test_release_import_captures_python_bytecode_as_an_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "Version.hh").write_text("frozen\n", encoding="utf-8")
            (source / "CLAUDE.md").write_text("local agent instructions\n", encoding="utf-8")
            claude = source / ".claude"
            claude.mkdir()
            (claude / "settings.json").write_text("{}\n", encoding="utf-8")
            bytecode = source / "__pycache__"
            bytecode.mkdir()
            (bytecode / "Version.cpython-312.pyc").write_bytes(b"host-generated")
            campaign = root / "campaign"
            (campaign / "flow").mkdir(parents=True)
            (campaign / "evidence").mkdir(parents=True)
            (campaign / "parent.json").write_text(
                json.dumps(
                    {
                        "parent_id": "parent",
                        "source_hash": "hash",
                        "source_commit": "commit",
                        "metrics": {"tns_abs_ns": 1.0},
                        "evaluation_mode": "power_only",
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "contract.json").write_text(
                json.dumps(
                    {
                        "metrics": [
                            {"name": "tns_abs_ns", "baseline": 2.0, "target": 1.0}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "flow" / "metrics.csv").write_text(
                "tns,total_power,leakage_power,slew_over_count,tool_runtime\n-1,2,1,0,1\n",
                encoding="utf-8",
            )
            (campaign / "flow" / "evaluate.tcl").write_text(
                "puts {minimal flow}\n",
                encoding="utf-8",
            )
            (campaign / "evidence" / "evidence.json").write_text(
                json.dumps({"checks": []}),
                encoding="utf-8",
            )

            selection = release_import.Selection(
                "test_artifact",
                "test_design",
                "campaign",
                "../source",
                "flow",
                "evidence",
                "test_design/test_artifact",
                "test_design/test_artifact",
            )
            lineage = root / "lineage"
            expected = root / "expected"
            with patch.object(release_import, "PROJECT_ROOT", root), patch.object(
                release_import, "LINEAGE_ROOT", lineage
            ), patch.object(release_import, "EXPECTED_ROOT", expected):
                release_import.import_selection(root, selection, copy_sources=True)

            manifest = json.loads(
                (expected / "test_design/test_artifact/source_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["capture_excludes"],
                ["__pycache__", ".agents", ".claude", ".gemini", "AGENTS.md", "CLAUDE.md"],
            )
            self.assertEqual(manifest["regular_file_count"], 1)

    def test_release_import_uses_selected_candidate_metadata(self) -> None:
        """A round/student record must not inherit stale campaign-parent metadata."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "Version.hh").write_text("frozen\n", encoding="utf-8")
            campaign = root / "campaign"
            (campaign / "flow").mkdir(parents=True)
            (campaign / "evidence").mkdir(parents=True)
            (campaign / "parent.json").write_text(
                json.dumps(
                    {
                        "parent_id": "stale-parent",
                        "source_hash": "stale-hash",
                        "source_commit": "stale-commit",
                        "metrics": {"tns_abs_ns": 99.0},
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "flow" / "candidate.json").write_text(
                json.dumps(
                    {
                        "parent_id": "round_058:student_1",
                        "source_hash": "selected-hash",
                        "source_commit": "selected-commit",
                        "metrics": {"tns_abs_ns": 1.0},
                        "artifacts": {"candidate_source": "/private/runtime/source"},
                        "hypothesis": {
                            "evaluation_mode": "power_then_timing",
                            "timing_recipe_id": "mt1_deep",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "contract.json").write_text(
                json.dumps(
                    {
                        "metrics": [
                            {"name": "tns_abs_ns", "baseline": 2.0, "target": 1.0}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "flow" / "metrics.csv").write_text(
                "tns,total_power,leakage_power,slew_over_count,tool_runtime\n-1,2,1,0,1\n",
                encoding="utf-8",
            )
            (campaign / "flow" / "evaluate.tcl").write_text("puts {minimal flow}\n", encoding="utf-8")
            (campaign / "evidence" / "evidence.json").write_text(
                json.dumps({"checks": []}), encoding="utf-8"
            )

            selection = release_import.Selection(
                "selected_artifact",
                "test_design",
                "campaign",
                "../source",
                "flow",
                "evidence",
                "test_design/selected_artifact",
                "test_design/selected_artifact",
                candidate_relative="flow/candidate.json",
            )
            lineage = root / "lineage"
            expected = root / "expected"
            with patch.object(release_import, "PROJECT_ROOT", root), patch.object(
                release_import, "LINEAGE_ROOT", lineage
            ), patch.object(release_import, "EXPECTED_ROOT", expected):
                release_import.import_selection(root, selection, copy_sources=True)

            selected = json.loads(
                (expected / "test_design/selected_artifact/ae2_selection.json").read_text(
                    encoding="utf-8"
                )
            )
            candidate = json.loads(
                (expected / "test_design/selected_artifact/candidate.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(selected["parent"]["parent_id"], "round_058:student_1")
            self.assertEqual(selected["parent"]["source_commit"], "selected-commit")
            self.assertEqual(selected["parent"]["evaluation_mode"], "power_then_timing")
            self.assertEqual(selected["parent"]["timing_recipe_id"], "mt1_deep")
            self.assertNotIn("artifacts", selected["parent"])
            self.assertEqual(candidate["metrics"], {"tns_abs_ns": 1.0})

    def test_ae2_stages_a_private_source_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "frozen-source"
            source.mkdir()
            version = source / "Version.hh"
            version.write_text("frozen\n", encoding="utf-8")

            staged = runner._stage_source(source=source, workspace=root / "generated" / "source")
            (staged / "Version.hh").write_text("generated\n", encoding="utf-8")

            self.assertEqual(version.read_text(encoding="utf-8"), "frozen\n")
            self.assertEqual((staged / "Version.hh").read_text(encoding="utf-8"), "generated\n")

    def test_eight_artifact_release_is_complete_and_buildable(self) -> None:
        report = ae1(artifact=_artifact("aes_r58_student1"))
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(report["checks"]["benchmark_jpeg_encoder"])
        self.assertTrue(report["checks"]["benchmark_nvdla_p"])
        self.assertTrue(report["checks"]["shared_openroad_p0"])
        self.assertTrue(report["tools"]["cmake"])
        self.assertTrue(report["tools"]["python3"])
        self.assertEqual(len(str(report["portable_tcl_sha256"])), 64)
        self.assertEqual(len(report["released_artifacts"]), 8)

    def test_every_released_artifact_has_a_portable_flow_and_verified_source(self) -> None:
        artifacts = _artifacts()
        self.assertEqual(len(artifacts), 8)
        for artifact_id, artifact in artifacts.items():
            expected = runner._path(str(artifact["expected_root"]))
            source = runner._path(str(artifact["source_root"]))
            benchmark = runner._path(str(artifact["benchmark_root"]))
            tcl = (expected / "evaluate.tcl").read_text(encoding="utf-8")
            selection = json.loads((expected / "ae2_selection.json").read_text(encoding="utf-8"))
            candidate = json.loads((expected / "candidate.json").read_text(encoding="utf-8"))
            self.assertIn("proc goalevolve_path", tcl, artifact_id)
            self.assertIn("GOALEVOLVE_BENCHMARK_ROOT", tcl, artifact_id)
            self.assertIn("third_party official_checker", tcl, artifact_id)
            self.assertNotIn("vendor/mlcad2026_official", tcl, artifact_id)
            self.assertNotIn("/home/haixuliu", tcl, artifact_id)
            self.assertNotIn('""" +', tcl, artifact_id)
            self.assertTrue((expected / "ae2_selection.json").is_file(), artifact_id)
            self.assertEqual(candidate["metrics"], selection["parent"]["metrics"], artifact_id)
            if "parent_id" in candidate:
                self.assertEqual(candidate["parent_id"], selection["parent"]["parent_id"], artifact_id)
            self.assertEqual(len(selection["goal_distances"]), 3, artifact_id)
            self.assertEqual(artifact["evaluation_mode"], selection["parent"]["evaluation_mode"], artifact_id)
            self.assertTrue(runner._snapshot_matches(source=source, manifest_path=expected / "source_manifest.json"), artifact_id)
            self.assertTrue((benchmark / f"{artifact['design']}.v").is_file(), artifact_id)

    def test_every_released_artifact_refreshes_stage_power_after_fresh_parasitics(self) -> None:
        """Post-place/route reports must not reuse OpenSTA's prior power cache."""
        for artifact_id, artifact in _artifacts().items():
            expected = runner._path(str(artifact["expected_root"]))
            tcl = (expected / "evaluate.tcl").read_text(encoding="utf-8")
            refresh_set = "set_power_activity -global -activity 0.1 -duty 0.5"
            refresh_unset = "unset_power_activity -global"
            placement_padding = tcl.index("set_placement_padding -global -left 0 -right 0")
            placement = tcl.index("detailed_placement", placement_padding)
            improve = tcl.index("improve_placement -max_displacement {5 1}", placement)
            mirror = tcl.index("optimize_mirroring", improve)
            placement_check = tcl.index("check_placement -verbose", mirror)
            placement_rc = tcl.index("estimate_parasitics -placement", placement_check)
            placement_refresh_set = tcl.index(refresh_set, placement_rc)
            placement_refresh_unset = tcl.index(refresh_unset, placement_refresh_set)
            placement_report = tcl.index("GOALEVOLVE_CHECKPOINT_BEGIN post_placement", placement_refresh_unset)
            route = tcl.index("global_route", placement_report)
            route_rc = tcl.index("estimate_parasitics -global_routing", route)
            route_refresh_set = tcl.index(refresh_set, route_rc)
            route_refresh_unset = tcl.index(refresh_unset, route_refresh_set)
            route_report = tcl.index("GOALEVOLVE_CHECKPOINT_BEGIN post_route", route_refresh_unset)

            self.assertLess(placement_padding, placement, artifact_id)
            self.assertLess(placement, improve, artifact_id)
            self.assertLess(improve, mirror, artifact_id)
            self.assertLess(mirror, placement_check, artifact_id)
            self.assertLess(placement_check, placement_rc, artifact_id)
            self.assertLess(placement_rc, placement_refresh_set, artifact_id)
            self.assertLess(placement_refresh_set, placement_refresh_unset, artifact_id)
            self.assertLess(placement_refresh_unset, placement_report, artifact_id)
            self.assertLess(route, route_rc, artifact_id)
            self.assertLess(route_rc, route_refresh_set, artifact_id)
            self.assertLess(route_refresh_set, route_refresh_unset, artifact_id)
            self.assertLess(route_refresh_unset, route_report, artifact_id)
            self.assertEqual(tcl.count(refresh_set), 2, artifact_id)
            self.assertEqual(tcl.count(refresh_unset), 2, artifact_id)
            self.assertGreaterEqual(tcl.count("report_power -digits 12"), 3, artifact_id)

    def test_ae2_materializes_rmp_liberty_only_for_rmp_recipes(self) -> None:
        benchmark = runner.PROJECT_ROOT / "third_party/benchmarks/benchmarks/aes_cipher_top"
        rmp_tcl = runner.PROJECT_ROOT / "artifact_evaluation/expected/aes_cipher_top/r058_student1/evaluate.tcl"
        plain_tcl = runner.PROJECT_ROOT / "artifact_evaluation/expected/ariane/r011_student3/evaluate.tcl"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            combined = runner._materialize_rmp_liberty(
                tcl=rmp_tcl,
                benchmark_root=benchmark,
                output=output,
            )
            self.assertIsNotNone(combined)
            assert combined is not None
            self.assertTrue(combined.is_file())
            manifest = json.loads((output / "rmp_standard_cells.manifest.json").read_text(encoding="utf-8"))
            self.assertGreater(manifest["cell_group_count"], 0)
            self.assertEqual(
                runner._materialize_rmp_liberty(
                    tcl=plain_tcl,
                    benchmark_root=benchmark,
                    output=output,
                ),
                None,
            )

    def test_git_index_contains_every_frozen_regular_source_file(self) -> None:
        for artifact_id, artifact in _artifacts().items():
            source = runner._path(str(artifact["source_root"]))
            expected = runner._path(str(artifact["expected_root"]))
            manifest = json.loads((expected / "source_manifest.json").read_text(encoding="utf-8"))
            relative_source = source.relative_to(runner.PROJECT_ROOT)
            output = subprocess.check_output(
                ["git", "ls-files", "-s", "--", str(relative_source)],
                cwd=runner.PROJECT_ROOT,
                text=True,
            )
            tracked_regular_files = sum(line.startswith("100") for line in output.splitlines())
            self.assertEqual(tracked_regular_files, manifest["regular_file_count"], artifact_id)

    def test_ae2_accepts_a_prepared_host_toolchain_when_no_private_record_exists(self) -> None:
        environment = runner._toolchain_environment({"PATH": "/host/bin", "LD_LIBRARY_PATH": "/host/lib"})
        self.assertEqual(environment["PATH"], "/host/bin")
        self.assertEqual(environment["LD_LIBRARY_PATH"], "/host/lib")

    def test_ae2_rejects_a_source_that_does_not_match_its_release_manifest(self) -> None:
        artifact = runner._artifact("aes_r58_student1")
        from unittest.mock import patch

        with patch.object(runner, "_snapshot_matches", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "does not match its release manifest"):
                runner.ae2(
                    artifact=artifact,
                    openroad=Path("/bin/true"),
                    jobs=1,
                    rebuild=False,
                    verbose=False,
                )

    def test_ae2_does_not_accept_an_unproven_external_openroad_binary(self) -> None:
        artifact = runner._artifact("aes_r58_student1")
        with self.assertRaisesRegex(RuntimeError, "per-artifact OpenROAD build"):
            runner.ae2(
                artifact=artifact,
                openroad=Path("/bin/true"),
                jobs=1,
                rebuild=False,
                verbose=False,
            )

    def test_ae1_does_not_require_a_standalone_cmake_binary(self) -> None:
        artifact = runner._artifact("aes_r58_student1")
        from unittest.mock import patch

        with patch.object(runner.shutil, "which", side_effect=lambda command: "/usr/bin/python3" if command == "python3" else None):
            result = runner.ae1(artifact=artifact)
        self.assertTrue(result["passed"])
        self.assertFalse(result["tools"]["cmake"])

if __name__ == "__main__":
    unittest.main()
