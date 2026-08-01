from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from artifact_evaluation import runner
from artifact_evaluation.runner import _artifact, ae1


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

    def test_aes_r54_release_is_complete_and_buildable(self) -> None:
        report = ae1(artifact=_artifact("aes_r54_student1"))
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(report["checks"]["benchmark_jpeg_encoder"])
        self.assertTrue(report["checks"]["benchmark_nvdla_p"])
        self.assertTrue(report["checks"]["shared_openroad_p0"])
        self.assertTrue(report["tools"]["cmake"])
        self.assertTrue(report["tools"]["python3"])
        self.assertEqual(len(str(report["portable_tcl_sha256"])), 64)

    def test_ae2_accepts_a_prepared_host_toolchain_when_no_private_record_exists(self) -> None:
        environment = runner._toolchain_environment({"PATH": "/host/bin", "LD_LIBRARY_PATH": "/host/lib"})
        self.assertEqual(environment["PATH"], "/host/bin")
        self.assertEqual(environment["LD_LIBRARY_PATH"], "/host/lib")

    def test_ae1_does_not_require_a_standalone_cmake_binary(self) -> None:
        artifact = runner._artifact("aes_r54_student1")
        from unittest.mock import patch

        with patch.object(runner.shutil, "which", side_effect=lambda command: "/usr/bin/python3" if command == "python3" else None):
            result = runner.ae1(artifact=artifact)
        self.assertTrue(result["passed"])
        self.assertFalse(result["tools"]["cmake"])

if __name__ == "__main__":
    unittest.main()
