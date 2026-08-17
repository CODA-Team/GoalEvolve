from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from goalevolve.evaluation import contest2026


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_default_readme_links_to_chinese_and_orders_ae_directories() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    structure = readme.split("## Code Structure", 1)[1].split("## Dependencies", 1)[0]

    assert "English · [中文](README.zh-CN.md)" in readme
    assert structure.index("ae1/") < structure.index("ae2/") < structure.index("ae3/") < structure.index("ae4/")
    assert "make check" in readme
    assert "artifact_evaluation/ae2/run_ae2.py preflight" in readme
    assert "artifact_evaluation/ae2/run_ae2.py replay" in readme
    assert "artifact_evaluation/ae4/run_ae4.py" in readme
    assert "artifact_evaluation.runner" not in readme

    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    check_script = (PROJECT_ROOT / "scripts" / "human" / "check.sh").read_text(encoding="utf-8")
    assert "artifact_evaluation/ae2/run_ae2.py preflight" in makefile
    assert "aes_cipher_top_student_code" in makefile
    assert "artifact_evaluation/ae1/run_ae1.py" in check_script
    assert "artifact_evaluation.runner" not in makefile + check_script


def test_third_party_readme_omits_unnecessary_upgrade_instruction() -> None:
    readme = (PROJECT_ROOT / "third_party" / "README.md").read_text(encoding="utf-8")
    assert "Upgrade it only as an explicit third-party update with recorded upstream provenance." not in readme


def test_ae3_sfinal_observer_persists_without_terminal_output(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    output.mkdir()
    stream = io.StringIO()
    with patch.object(contest2026, "observe_sfinal", return_value={"score": {"Sfinal": 1.25}}):
        with redirect_stdout(stream):
            result = contest2026._observe_sfinal(
                design="aes_cipher_top",
                benchmark_dir=tmp_path / "benchmark",
                output=output,
            )

    assert result == {"sfinal_observation": str(output / "sfinal_observation.json")}
    assert stream.getvalue() == ""
