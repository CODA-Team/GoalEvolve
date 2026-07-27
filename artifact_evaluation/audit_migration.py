#!/usr/bin/env python3
"""Compare the reorganized package against a GoalEvolve_v2 reference tree.

This is an audit utility, not part of the released runtime. It parses Python
ASTs so it can distinguish import relocation from changed class/function
definitions. Run it from the GoalEvolve repository:

    python3 artifact_evaluation/audit_migration.py \
      --reference ../GoalEvolve_v2 --format markdown
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path


MODULES = {
    "contracts": "core/contracts",
    "io": "core/io",
    "models": "core/models",
    "plugins": "core/plugins",
    "provenance": "core/provenance",
    "diagnosis": "planning/diagnosis",
    "epd": "planning/epd",
    "observations": "planning/observations",
    "retrieval": "planning/retrieval",
    "scope": "planning/scope",
    "timing_recovery": "planning/timing_recovery",
    "prompting": "agents/prompting",
    "teacher": "agents/teacher",
    "codex_runtime": "agents/codex_runtime",
    "codex_student": "agents/codex_student",
    "execution": "execution/execution",
    "preflight": "execution/preflight",
    "workspace": "execution/workspace",
    "engine": "execution/engine",
    "contest2026": "evaluation/contest2026",
    "evidence": "evaluation/evidence",
    "leaderboard": "evaluation/leaderboard",
    "promotion": "evaluation/promotion",
    "sfinal": "evaluation/sfinal",
    "evaluators": "testing/evaluators",
    "cli": "cli",
    "legacy": "legacy",
    "token_ledger": "token_ledger",
    "config": "config",
}


def _without_relocated_imports(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(
        line
        for line in lines
        if not line.startswith(("from .", "from goalevolve", "from goalevolve_v2"))
    ) + "\n"


def _definitions(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: dict[str, str] = {}

    def collect(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{node.name}"
                result[name] = ast.dump(node, annotate_fields=True, include_attributes=False)
                if isinstance(node, ast.ClassDef):
                    collect(node.body, f"{name}.")

    collect(tree.body)
    return result


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit(reference: Path, project: Path) -> list[dict[str, object]]:
    reference_package = reference / "goalevolve_v2"
    project_package = project / "goalevolve"
    result: list[dict[str, object]] = []
    for old, new in MODULES.items():
        old_path = reference_package / f"{old}.py"
        new_path = project_package / f"{new}.py"
        old_defs = _definitions(old_path)
        new_defs = _definitions(new_path)
        changed = sorted(name for name in old_defs.keys() & new_defs.keys() if old_defs[name] != new_defs[name])
        result.append(
            {
                "reference": str(old_path),
                "reorganized": str(new_path),
                "module": old,
                "mapping": new,
                "normalized_body_equal": _without_relocated_imports(old_path) == _without_relocated_imports(new_path),
                "missing_definitions": sorted(old_defs.keys() - new_defs.keys()),
                "extra_definitions": sorted(new_defs.keys() - old_defs.keys()),
                "changed_definitions": changed,
                "reference_normalized_sha256": _digest(_without_relocated_imports(old_path)),
                "reorganized_normalized_sha256": _digest(_without_relocated_imports(new_path)),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    report = audit(args.reference.resolve(), args.project.resolve())
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print("# GoalEvolve Migration Audit")
    print()
    print("| v2 module | reorganized module | normalized body | definition changes |")
    print("| --- | --- | --- | --- |")
    for entry in report:
        changed = [*entry["missing_definitions"], *entry["extra_definitions"], *entry["changed_definitions"]]
        print(
            f"| `{entry['module']}` | `{entry['mapping']}` | "
            f"{'equal' if entry['normalized_body_equal'] else 'different'} | "
            f"{', '.join(changed) if changed else 'none'} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
