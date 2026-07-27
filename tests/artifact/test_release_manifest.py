from __future__ import annotations

import unittest

from artifact_evaluation.runner import _artifact, ae1


class ReleaseArtifactTests(unittest.TestCase):
    def test_aes_r54_release_is_complete_and_buildable(self) -> None:
        report = ae1(artifact=_artifact("aes_r54_student1"))
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(report["checks"]["benchmark_jpeg_encoder"])
        self.assertTrue(report["checks"]["benchmark_nvdla_p"])
        self.assertTrue(report["checks"]["shared_openroad_p0"])
        self.assertTrue(report["tools"]["cmake"])
        self.assertTrue(report["tools"]["python3"])
        self.assertEqual(len(str(report["portable_tcl_sha256"])), 64)


if __name__ == "__main__":
    unittest.main()
