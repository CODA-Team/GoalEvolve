from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = PROJECT_ROOT / "artifact_evaluation"


def test_artifact_evaluation_has_one_entry_directory_per_ae() -> None:
    assert (ARTIFACT_ROOT / "ae1" / "run_ae1.py").is_file()
    assert (ARTIFACT_ROOT / "ae2" / "run_ae2.py").is_file()
    assert (ARTIFACT_ROOT / "ae3" / "README.md").is_file()
    assert (ARTIFACT_ROOT / "ae4" / "run_ae4.py").is_file()
    assert not (ARTIFACT_ROOT / "runner.py").exists()


def test_ae3_documents_the_real_user_evolution_entrypoint() -> None:
    readme = (ARTIFACT_ROOT / "ae3" / "README.md").read_text(encoding="utf-8")
    assert "goalevolve.cli run" in readme
    assert "goalevolve/" in readme
    assert "experiments/" in readme
