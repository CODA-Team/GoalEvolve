from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_ae4_module():
    module_path = PROJECT_ROOT / "artifact_evaluation" / "ae4" / "run_ae4.py"
    spec = importlib.util.spec_from_file_location("ae4_run", module_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ae4_resolves_moved_project_root_and_public_aes_artifact() -> None:
    module = load_ae4_module()
    assert module.PROJECT_ROOT == PROJECT_ROOT
    assert module.RESULTS_ROOT == PROJECT_ROOT / "outputs" / "ae4"
    config = module.load_config()
    assert config["source_binary"]["ae2_artifact"] == "aes_cipher_top_student_code"
    assert "student_code" in config["source_binary"]["path"]
    assert "student_code" in config["source_binary"]["report"]
