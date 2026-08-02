from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from artifact_evaluation import runner
from artifact_evaluation.runner import _artifact, _artifacts, ae1


class ReleaseArtifactTests(unittest.TestCase):
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
        report = ae1(artifact=_artifact("aes_r54_student1"))
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
            self.assertNotIn('""" +', tcl, artifact_id)
            self.assertTrue((expected / "ae2_selection.json").is_file(), artifact_id)
            self.assertEqual(candidate["metrics"], selection["parent"]["metrics"], artifact_id)
            if "parent_id" in candidate:
                self.assertEqual(candidate["parent_id"], selection["parent"]["parent_id"], artifact_id)
            self.assertEqual(len(selection["goal_distances"]), 3, artifact_id)
            self.assertEqual(artifact["evaluation_mode"], selection["parent"]["evaluation_mode"], artifact_id)
            self.assertTrue(runner._snapshot_matches(source=source, manifest_path=expected / "source_manifest.json"), artifact_id)
            self.assertTrue((benchmark / f"{artifact['design']}.v").is_file(), artifact_id)

    def test_ae2_accepts_a_prepared_host_toolchain_when_no_private_record_exists(self) -> None:
        environment = runner._toolchain_environment({"PATH": "/host/bin", "LD_LIBRARY_PATH": "/host/lib"})
        self.assertEqual(environment["PATH"], "/host/bin")
        self.assertEqual(environment["LD_LIBRARY_PATH"], "/host/lib")

    def test_ae2_rejects_a_source_that_does_not_match_its_release_manifest(self) -> None:
        artifact = runner._artifact("aes_r54_student1")
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
        artifact = runner._artifact("aes_r54_student1")
        with self.assertRaisesRegex(RuntimeError, "per-artifact OpenROAD build"):
            runner.ae2(
                artifact=artifact,
                openroad=Path("/bin/true"),
                jobs=1,
                rebuild=False,
                verbose=False,
            )

    def test_ae1_does_not_require_a_standalone_cmake_binary(self) -> None:
        artifact = runner._artifact("aes_r54_student1")
        from unittest.mock import patch

        with patch.object(runner.shutil, "which", side_effect=lambda command: "/usr/bin/python3" if command == "python3" else None):
            result = runner.ae1(artifact=artifact)
        self.assertTrue(result["passed"])
        self.assertFalse(result["tools"]["cmake"])

if __name__ == "__main__":
    unittest.main()
