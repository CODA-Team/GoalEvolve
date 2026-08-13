#!/usr/bin/env python3
"""Import the eight selected GoalEvolve_v2 parents into AE-2 release inputs.

This maintainer utility is deliberately separate from ``runner.py``.  It is
used only while curating a release from a local GoalEvolve_v2 record tree; a
clone of this repository consumes the resulting immutable files directly.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from artifact_evaluation.verify_openroad_snapshot import snapshot_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LINEAGE_ROOT = PROJECT_ROOT / "artifact_evaluation" / "lineage"
EXPECTED_ROOT = PROJECT_ROOT / "artifact_evaluation" / "expected"
CAPTURE_EXCLUDES = ("__pycache__", ".agents", ".claude", ".gemini", "AGENTS.md", "CLAUDE.md")


@dataclass(frozen=True)
class Selection:
    artifact_id: str
    design: str
    campaign: str
    source_relative: str
    tcl_relative: str
    evidence_relative: str
    lineage_relative: str
    expected_relative: str
    preserve_existing_release: bool = False
    candidate_relative: str | None = None
    selected_parent_id: str | None = None


SELECTIONS = (
    Selection(
        "aes_r58_student1",
        "aes_cipher_top",
        "runtime/aes_campaign_power_target",
        "rounds/round_058/students/student_1/workspace/source",
        "rounds/round_058/students/student_1/artifacts/contest_output",
        "rounds/round_058/students/student_1/artifacts",
        "aes_cipher_top/r058_student1",
        "aes_cipher_top/r058_student1",
        candidate_relative="rounds/round_058/students/student_1/artifacts/candidate.json",
        selected_parent_id="round_058:student_1",
    ),
    Selection(
        "jpeg_r16_student1",
        "jpeg_encoder",
        "runtime/jpeg_campaign",
        "parents/1d76634cfe95582ddb6f43a429eaa1f6907136b33414fe586be2e95ad9adb1f0/source",
        "rounds/round_016/students/student_1/artifacts/contest_output",
        "rounds/round_016/students/student_1/artifacts",
        "jpeg_encoder/r016_student1",
        "jpeg_encoder/r016_student1",
    ),
    Selection(
        "ariane_r11_student3",
        "ariane",
        "runtime/ariane_evolution/campaign_r58_lpower_17600000000",
        "parents/1d6475a8066ae156c2211d549f5fa1bc38edee9de10bc77813cea029a18c9c63/source",
        "rounds/round_011/students/student_3/artifacts/contest_output",
        "rounds/round_011/students/student_3/artifacts",
        "ariane/r011_student3",
        "ariane/r011_student3",
    ),
    Selection(
        "mempool_aes_r58_parent",
        "mempool_group",
        "runtime/mempool_group_campaign/evolution_r58_target_1850",
        "parents/ef789dcbfcd67a2aa693497244fccdbb535becdc320aa4011d804a93cc7f6b2d/source",
        "stage_baselines/ef789dcbfcd67a2aa693497244fccdbb535becdc320aa4011d804a93cc7f6b2d_power_then_timing_94d03f611279ceab",
        "stage_baselines/ef789dcbfcd67a2aa693497244fccdbb535becdc320aa4011d804a93cc7f6b2d_power_then_timing_94d03f611279ceab",
        "mempool_group/aes_r58_parent",
        "mempool_group/aes_r58_parent",
    ),
    Selection(
        "nvdla_a_r16_student1",
        "nvdla_a",
        "runtime/nvdla_a_evolution/campaign_aes_r58_power_target",
        "rounds/round_016/students/student_1/workspace/source",
        "rounds/round_016/students/student_1/artifacts/contest_output",
        "rounds/round_016/students/student_1/artifacts",
        "nvdla_a/r016_student1",
        "nvdla_a/r016_student1",
        candidate_relative="rounds/round_016/students/student_1/artifacts/candidate.json",
        selected_parent_id="round_016:student_1",
    ),
    Selection(
        "nvdla_c_rmp_path_cone_halo_timing",
        "nvdla_c",
        "runtime/nvdla_c_evolution/campaign_schedule_locked_rmp_v1",
        "../campaign_r58_drv499_v2/parents/baseline/source",
        "../campaign_r58_drv499_v2/stage_baselines/baseline_timing_only_rmp_path_cone_halo_timing_efaf3f024348efb7",
        "../campaign_r58_drv499_v2/stage_baselines/baseline_timing_only_rmp_path_cone_halo_timing_efaf3f024348efb7",
        "nvdla_c/rmp_path_cone_halo_timing",
        "nvdla_c/rmp_path_cone_halo_timing",
    ),
    Selection(
        "nvdla_m_r11_student1",
        "nvdla_m",
        "runtime/nvdla_m_campaign/evolution",
        "rounds/round_011/students/student_1/workspace/source",
        "rounds/round_011/students/student_1/artifacts/contest_output",
        "rounds/round_011/students/student_1/artifacts",
        "nvdla_m/r011_student1",
        "nvdla_m/r011_student1",
        candidate_relative="rounds/round_011/students/student_1/artifacts/candidate.json",
        selected_parent_id="round_011:student_1",
    ),
    Selection(
        "nvdla_p_r4_student1",
        "nvdla_p",
        "runtime/nvdla_p_campaign/evolution_top1_restart",
        "rounds/round_004/students/student_1/workspace/source",
        "rounds/round_004/students/student_1/artifacts/contest_output",
        "rounds/round_004/students/student_1/artifacts",
        "nvdla_p/r004_student1",
        "nvdla_p/r004_student1",
        candidate_relative="rounds/round_004/students/student_1/artifacts/candidate.json",
        selected_parent_id="round_004:student_1",
    ),
)


def _read_json(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _metrics(path: Path) -> dict[str, float]:
    with path.open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    total = float(row["total_power"])
    leakage = float(row["leakage_power"])
    return {
        "tns_abs_ns": abs(float(row["tns"])),
        "dynamic_power_pw": total - leakage,
        "leakage_power_pw": leakage,
        "drv_count": float(row["slew_over_count"]),
        "runtime_s": float(row["tool_runtime"]),
    }


def _portable_tcl(original: str, *, benchmark_root: str, output_root: str, project_root: str) -> str:
    roots = (benchmark_root, output_root, project_root + "/vendor")

    def replace(match: re.Match[str]) -> str:
        value = match.group(1)
        portable_root = value.startswith(("__BENCHMARK_ROOT__", "__OUTPUT_ROOT__", "__PROJECT_VENDOR__"))
        return f"[goalevolve_path {{{value}}}]" if portable_root or any(root in value for root in roots) else match.group(0)

    body = original.replace(benchmark_root, "__BENCHMARK_ROOT__").replace(
        output_root, "__OUTPUT_ROOT__"
    ).replace(
        project_root + "/vendor/mlcad2026_official",
        "__PROJECT_VENDOR__/mlcad2026_official",
    ).replace(
        project_root + "/vendor/official_checker",
        "__PROJECT_VENDOR__/official_checker",
    )
    body = re.sub(r"\{([^{}\n]+)\}", replace, body)
    header = """# Portable Tcl generated from a recorded GoalEvolve_v2 parent flow.
foreach required {GOALEVOLVE_BENCHMARK_ROOT GOALEVOLVE_PROJECT_ROOT GOALEVOLVE_AE_OUTPUT} {
  if {![info exists ::env($required)] || $::env($required) eq \"\"} {
    error \"artifact evaluation requires environment variable $required\"
  }
}
proc goalevolve_path {recorded_path} {
  set substitutions [list \\
    \"__BENCHMARK_ROOT__\" $::env(GOALEVOLVE_BENCHMARK_ROOT) \\
    \"__PROJECT_VENDOR__/mlcad2026_official\" [file join $::env(GOALEVOLVE_PROJECT_ROOT) third_party official_checker] \\
    \"__PROJECT_VENDOR__\" [file join $::env(GOALEVOLVE_PROJECT_ROOT) third_party] \\
    \"__OUTPUT_ROOT__\" $::env(GOALEVOLVE_AE_OUTPUT)]
  return [string map $substitutions $recorded_path]
}

"""
    return header + body


def _residual(value: float, baseline: float, target: float) -> float:
    return max(0.0, value - target) / max(abs(baseline - target), abs(baseline), 1.0)


def _unmet_percent(value: float, target: float) -> float:
    return 100.0 * max(0.0, value - target) / max(abs(target), 1.0)


def _copy_file(source: Path, destination: Path) -> None:
    if source.is_file():
        shutil.copy2(source, destination)


def import_selection(reference: Path, selection: Selection, *, copy_sources: bool) -> dict[str, Any]:
    campaign = (reference / selection.campaign).resolve()
    parent = _read_json(campaign / "parent.json")
    candidate = (
        _read_json((campaign / selection.candidate_relative).resolve())
        if selection.candidate_relative
        else {}
    )
    contract = _read_json(campaign / "contract.json")
    source = (campaign / selection.source_relative).resolve()
    tcl_root = (campaign / selection.tcl_relative).resolve()
    evidence_root = (campaign / selection.evidence_relative).resolve()
    lineage = LINEAGE_ROOT / selection.lineage_relative
    expected = EXPECTED_ROOT / selection.expected_relative
    expected.mkdir(parents=True, exist_ok=True)
    destination_source = lineage / "source"
    if copy_sources and not selection.preserve_existing_release:
        if destination_source.exists():
            shutil.rmtree(destination_source)
        shutil.copytree(source, destination_source, symlinks=True)
    if not destination_source.is_dir():
        raise FileNotFoundError(destination_source)
    # Python bytecode is host-generated evaluation debris, not source
    # provenance.  Keep release capture aligned with the verifier semantics.
    capture_excludes = CAPTURE_EXCLUDES
    reference_source_manifest = snapshot_metadata(
        source,
        capture_excludes=capture_excludes,
    )
    destination_source_manifest = snapshot_metadata(
        destination_source,
        capture_excludes=capture_excludes,
    )
    if reference_source_manifest != destination_source_manifest:
        raise RuntimeError(f"source snapshot mismatch for {selection.artifact_id}")
    actual_metrics = _metrics(tcl_root / "metrics.csv")
    selected_parent = dict(parent)
    # Candidate artifacts include local workspace and Codex paths.  Preserve
    # only the identity and measured-QoR fields that define a public AE-2
    # parent; the complete candidate hypothesis remains a separate audit file.
    for key in ("parent_id", "source_hash", "source_commit", "metrics", "evaluation_mode", "timing_recipe_id"):
        value = candidate.get(key)
        if value is not None:
            selected_parent[key] = value
    candidate_hypothesis = dict(candidate.get("hypothesis") or {})
    # A historical Student candidate may have been superseded by the campaign's
    # terminal parent.  Its executed mode and recipe live under the candidate
    # hypothesis, not in that later parent record.
    for key in ("evaluation_mode", "timing_recipe_id"):
        value = candidate.get(key) or candidate_hypothesis.get(key)
        if value is not None:
            selected_parent[key] = value
    if selection.candidate_relative:
        selected_parent["source_hash"] = candidate.get("source_hash") or reference_source_manifest["content_sha256"]
    selected_parent["parent_id"] = (
        selection.selected_parent_id
        or selected_parent.get("parent_id")
        or selection.artifact_id
    )
    parent_metrics = dict(selected_parent["metrics"])
    contract_metrics = {item["name"]: item for item in contract["metrics"]}
    normalized = {
        name: {
            "value": parent_metrics[name],
            "baseline": item["baseline"],
            "target": item["target"],
            "normalized_residual": _residual(parent_metrics[name], item["baseline"], item["target"]),
            "unmet_target_percent": _unmet_percent(parent_metrics[name], item["target"]),
        }
        for name, item in contract_metrics.items()
    }
    selected_parent["goal_distance"] = sum(
        float(row["normalized_residual"])
        for row in normalized.values()
    ) / max(len(normalized), 1)
    selected = {
        "schema": "goalevolve.ae2-selection.v1",
        "artifact_id": selection.artifact_id,
        "design": selection.design,
        "parent": selected_parent,
        "contract": contract,
        "recorded_flow_metrics": actual_metrics,
        "goal_distances": normalized,
        "provenance": {
            "reference_campaign": selection.campaign,
            "reference_source": selection.source_relative,
            "reference_tcl": selection.tcl_relative + "/evaluate.tcl",
            "reference_evidence": selection.evidence_relative,
            "reference_source_manifest": reference_source_manifest,
        },
    }
    _write_json(expected / "ae2_selection.json", selected)
    if not selection.preserve_existing_release:
        original_tcl = (tcl_root / "evaluate.tcl").read_text(encoding="utf-8")
        _copy_file(tcl_root / "evaluate.tcl", expected / "evaluate.original.tcl")
        benchmark_root = "/home/haixuliu/MLCAD26/MLCAD26-Contest-Scripts-Benchmarks"
        output_root = str(tcl_root)
        portable_tcl = _portable_tcl(
            original_tcl,
            benchmark_root=benchmark_root,
            output_root=output_root,
            project_root="/home/haixuliu/MLCAD26/GoalEvolve_v2",
        )
        (expected / "evaluate.tcl").write_text(portable_tcl, encoding="utf-8")
        for name in ("metrics.csv",):
            _copy_file(tcl_root / name, expected / name)
        evidence_file = evidence_root / ("baseline.json" if (evidence_root / "baseline.json").is_file() else "evidence.json")
        _write_json(
            expected / "candidate.json",
            {
                "artifact_id": selection.artifact_id,
                "design": selection.design,
                "parent_id": selected_parent["parent_id"],
                "source_hash": selected_parent["source_hash"],
                "source_commit": selected_parent["source_commit"],
                "metrics": parent_metrics,
            },
        )
        _write_json(expected / "metrics.json", {"decision_metrics": parent_metrics, "contract": contract})
        _write_json(
            expected / "evidence.json",
            {
                "artifact_id": selection.artifact_id,
                "checks": _read_json(evidence_file).get("checks", []),
                "provenance": selected["provenance"],
            },
        )
        _write_json(expected / "source_commit.json", {"source_commit": selected_parent["source_commit"], "source_hash": selected_parent["source_hash"]})
    _write_json(
        expected / "source_manifest.json",
        {"capture_excludes": list(capture_excludes), **destination_source_manifest},
    )
    return {
        "artifact_id": selection.artifact_id,
        "design": selection.design,
        "source_hash": selected_parent["source_hash"],
        "source_commit": selected_parent["source_commit"],
        "parent_id": selected_parent["parent_id"],
        "source_root": str((lineage / "source").relative_to(PROJECT_ROOT)),
        "expected_root": str(expected.relative_to(PROJECT_ROOT)),
        "benchmark_root": f"third_party/benchmarks/benchmarks/{selection.design}",
        "evaluation_mode": selected_parent.get("evaluation_mode", "post_route_global_route_estimate_parasitics"),
        "tolerances": {"tns_abs_ns": 0.05, "dynamic_power_pw": 1_000_000_000.0, "leakage_power_pw": 1_000_000.0},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--copy-sources", action="store_true")
    args = parser.parse_args()
    artifacts = {selection.artifact_id: import_selection(args.reference.resolve(), selection, copy_sources=args.copy_sources) for selection in SELECTIONS}
    _write_json(PROJECT_ROOT / "artifact_evaluation" / "release_manifest.json", {"schema": "goalevolve.release-manifest.v1", "artifacts": artifacts})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
