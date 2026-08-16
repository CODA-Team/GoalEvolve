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
from goalevolve.evaluation.contest2026 import _fresh_stage_qor_tcl


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


# Fresh replays after `_refresh_stage_qor_reports()` made post-placement and
# post-route power cache-safe.  Values are the authoritative post-route
# measurements in the normal parser unit (absolute TNS in ns, power in pW).
# Keep this curation input explicit and apply it atomically to every public
# record that the AE-2 runner, selection document, and paper-facing summaries
# consume.  Runtime and electrical-violation telemetry remain historical
# observer fields because the cache refresh does not change them.
CACHE_SAFE_REPLAY_METRICS: dict[str, dict[str, float]] = {
    "aes_r58_student1": {
        "tns_abs_ns": 15.572590769,
        "dynamic_power_pw": 335_607_141_000.0,
        "leakage_power_pw": 29_093_000.0,
        "total_power_pw": 335_636_234_000.0,
    },
    "ariane_r11_student3": {
        "tns_abs_ns": 688.536962490,
        "dynamic_power_pw": 588_058_613_000.0,
        "leakage_power_pw": 17_935_077_000.0,
        "total_power_pw": 605_993_690_000.0,
    },
    "jpeg_r16_student1": {
        "tns_abs_ns": 51.175317876,
        "dynamic_power_pw": 270_427_704_000.0,
        "leakage_power_pw": 114_525_000.0,
        "total_power_pw": 270_542_229_000.0,
    },
    "mempool_aes_r58_parent": {
        "tns_abs_ns": 2714.686920100,
        "dynamic_power_pw": 249_586_381_000.0,
        "leakage_power_pw": 3_059_457_000.0,
        "total_power_pw": 252_645_838_000.0,
    },
    "nvdla_a_r16_student1": {
        "tns_abs_ns": 90.640386651,
        "dynamic_power_pw": 144_228_574_000.0,
        "leakage_power_pw": 285_284_000.0,
        "total_power_pw": 144_513_858_000.0,
    },
    "nvdla_c_rmp_path_cone_halo_timing": {
        "tns_abs_ns": 7.247678218,
        "dynamic_power_pw": 584_086_037_000.0,
        "leakage_power_pw": 17_259_404_000.0,
        "total_power_pw": 601_345_441_000.0,
    },
    "nvdla_m_r11_student1": {
        "tns_abs_ns": 14.209663182,
        "dynamic_power_pw": 39_473_305_000.0,
        "leakage_power_pw": 38_326_000.0,
        "total_power_pw": 39_511_631_000.0,
    },
    "nvdla_p_r4_student1": {
        "tns_abs_ns": 163.493983868,
        "dynamic_power_pw": 38_231_478_000.0,
        "leakage_power_pw": 256_406_000.0,
        "total_power_pw": 38_487_884_000.0,
    },
}


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


def _refresh_stage_qor_reports(tcl: str) -> str:
    """Normalize the two authoritative post-optimization QoR boundaries.

    Historical campaigns reported power before detailed placement and then
    reused OpenSTA's instance-power cache after placement/global-route RC was
    re-estimated.  Keep the recorded optimization schedule intact, but make
    every *full recorded flow* use the fixed controller-owned tail.  The
    release-import helper is also used by provenance/unit fixtures that carry
    a deliberately minimal Tcl program rather than a GoalEvolve contest flow;
    those fixtures have no stage boundaries to normalize and must remain
    portable as supplied.  A partially marked flow remains an error below,
    because that would make a real replay ambiguous.
    """
    lines = tcl.splitlines()
    stage_markers = (
        'puts "GOALEVOLVE_CHECKPOINT_BEGIN post_placement"',
        'puts "GOALEVOLVE_CHECKPOINT_END post_placement"',
        'puts "GOALEVOLVE_CHECKPOINT_BEGIN post_route"',
        'puts "GOALEVOLVE_CHECKPOINT_END post_route"',
    )
    present_markers = {
        line.strip() for line in lines if line.strip() in set(stage_markers)
    }
    if not present_markers:
        return tcl if tcl.endswith("\n") else tcl + "\n"
    missing_markers = set(stage_markers) - present_markers
    if missing_markers:
        missing = ", ".join(sorted(missing_markers))
        raise ValueError(f"recorded Tcl has incomplete stage markers: {missing}")

    def index_exact(value: str, *, start: int = 0) -> int:
        for index in range(start, len(lines)):
            if lines[index].strip() == value:
                return index
        raise ValueError(f"recorded Tcl is missing required stage marker: {value}")

    placement_begin = index_exact('puts "GOALEVOLVE_CHECKPOINT_BEGIN post_placement"')
    placement_start = max(
        index
        for index in range(placement_begin)
        if lines[index].strip() == "detailed_placement"
    )
    route_begin = index_exact(
        'puts "GOALEVOLVE_CHECKPOINT_BEGIN post_route"',
        start=placement_begin,
    )
    if not any(
        lines[index].strip().startswith("set_routing_layers ")
        for index in range(placement_begin, route_begin)
    ):
        raise ValueError("recorded Tcl is missing global-route layer setup")
    placement_tail = [
        "set_placement_padding -global -left 0 -right 0",
        "detailed_placement",
        "improve_placement -max_displacement {5 1}",
        "optimize_mirroring",
        "check_placement -verbose",
        *_fresh_stage_qor_tcl(parasitics_command="estimate_parasitics -placement"),
    ]
    lines[placement_start:placement_begin] = placement_tail

    placement_begin = index_exact('puts "GOALEVOLVE_CHECKPOINT_BEGIN post_placement"')
    placement_end = index_exact(
        'puts "GOALEVOLVE_CHECKPOINT_END post_placement"',
        start=placement_begin,
    )
    route_begin = index_exact(
        'puts "GOALEVOLVE_CHECKPOINT_BEGIN post_route"',
        start=placement_end,
    )
    route_start = next(
        index
        for index in range(placement_end, route_begin)
        if lines[index].strip().startswith("global_route ")
    )
    route_command = lines[route_start].strip()
    route_tail = [
        route_command,
        *_fresh_stage_qor_tcl(parasitics_command="estimate_parasitics -global_routing"),
    ]
    lines[route_start:route_begin] = route_tail

    def normalize_power_report(stage: str) -> None:
        begin = index_exact(f'puts "GOALEVOLVE_CHECKPOINT_BEGIN {stage}"')
        end = index_exact(f'puts "GOALEVOLVE_CHECKPOINT_END {stage}"', start=begin)
        report_indexes = [
            index
            for index in range(begin + 1, end)
            if lines[index].strip().startswith("report_power")
        ]
        for index in reversed(report_indexes):
            del lines[index]
        end = index_exact(f'puts "GOALEVOLVE_CHECKPOINT_END {stage}"', start=begin)
        insertion = next(
            (
                index
                for index in range(begin + 1, end)
                if lines[index].strip().startswith(("write_verilog ", "write_db "))
            ),
            end,
        )
        lines.insert(insertion, "report_power -digits 12")

    normalize_power_report("post_placement")
    normalize_power_report("post_route")

    # The official parser consumes the last power table in the log.  Preserve
    # that convention while avoiding low-precision output in the final table.
    metrics_begin = index_exact('puts "===== METRICS ====="')
    for index in range(metrics_begin, len(lines)):
        if lines[index].strip() == "report_power":
            lines[index] = "report_power -digits 12"
            break
    return "\n".join(lines) + "\n"


def _residual(value: float, baseline: float, target: float) -> float:
    return max(0.0, value - target) / max(abs(baseline - target), abs(baseline), 1.0)


def _unmet_percent(value: float, target: float) -> float:
    return 100.0 * max(0.0, value - target) / max(abs(target), 1.0)


def _copy_file(source: Path, destination: Path) -> None:
    if source.is_file():
        shutil.copy2(source, destination)


def refresh_cache_safe_replay_records() -> dict[str, dict[str, float]]:
    """Synchronize fixed AE-2 expectations with cache-safe replay evidence.

    This is deliberately separate from historical source import: it leaves
    source snapshots, Tcl schedules, parent IDs, and observer telemetry intact
    while replacing only the three final decision metrics made authoritative by
    the corrected post-route reporting tail.
    """
    selected = {selection.artifact_id: selection for selection in SELECTIONS}
    if set(selected) != set(CACHE_SAFE_REPLAY_METRICS):
        raise RuntimeError("cache-safe metric set does not match the eight released selections")
    for artifact_id, selection in selected.items():
        values = dict(CACHE_SAFE_REPLAY_METRICS[artifact_id])
        decision = {key: values[key] for key in ("tns_abs_ns", "dynamic_power_pw", "leakage_power_pw")}
        expected = EXPECTED_ROOT / selection.expected_relative
        selection_path = expected / "ae2_selection.json"
        candidate_path = expected / "candidate.json"
        metrics_path = expected / "metrics.json"
        csv_path = expected / "metrics.csv"
        record = _read_json(selection_path)
        parent_metrics = dict(record["parent"]["metrics"])
        parent_metrics.update(decision)
        record["parent"]["metrics"] = parent_metrics
        recorded = dict(record.get("recorded_flow_metrics") or {})
        recorded.update(decision)
        record["recorded_flow_metrics"] = recorded
        record["cache_safe_replay_metrics"] = {
            **decision,
            "total_power_pw": values["total_power_pw"],
            "measurement_boundary": "post_route_global_route_estimate_parasitics_cache_refreshed",
        }
        contract_metrics = {item["name"]: item for item in record["contract"]["metrics"]}
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
        record["goal_distances"] = normalized
        record["parent"]["goal_distance"] = sum(
            float(row["normalized_residual"]) for row in normalized.values()
        ) / max(len(normalized), 1)
        _write_json(selection_path, record)

        candidate = _read_json(candidate_path)
        candidate_metrics = dict(candidate["metrics"])
        candidate_metrics.update(decision)
        candidate["metrics"] = candidate_metrics
        _write_json(candidate_path, candidate)

        metrics = _read_json(metrics_path)
        decision_metrics = dict(metrics["decision_metrics"])
        decision_metrics.update(decision)
        metrics["decision_metrics"] = decision_metrics
        _write_json(metrics_path, metrics)

        with csv_path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = list(reader.fieldnames or ())
            rows = list(reader)
        if not rows or not fieldnames:
            raise RuntimeError(f"missing metrics rows: {csv_path}")
        for row in rows:
            row["tns"] = f"{-decision['tns_abs_ns']:.12g}"
            row["leakage_power"] = f"{decision['leakage_power_pw']:.12g}"
            row["total_power"] = f"{values['total_power_pw']:.12g}"
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    return CACHE_SAFE_REPLAY_METRICS


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
        portable_tcl = _refresh_stage_qor_reports(
            _portable_tcl(
                original_tcl,
                benchmark_root=benchmark_root,
                output_root=output_root,
                project_root="/home/haixuliu/MLCAD26/GoalEvolve_v2",
            )
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
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--copy-sources", action="store_true")
    parser.add_argument(
        "--refresh-cache-safe-records",
        action="store_true",
        help="replace fixed AE-2 decision expectations with cache-safe replay measurements",
    )
    args = parser.parse_args()
    if args.refresh_cache_safe_records:
        if args.reference is not None or args.copy_sources:
            parser.error("--refresh-cache-safe-records cannot be combined with import arguments")
        print(json.dumps(refresh_cache_safe_replay_records(), indent=2, sort_keys=True))
        return 0
    if args.reference is None:
        parser.error("--reference is required when importing a historical record tree")
    artifacts = {selection.artifact_id: import_selection(args.reference.resolve(), selection, copy_sources=args.copy_sources) for selection in SELECTIONS}
    _write_json(PROJECT_ROOT / "artifact_evaluation" / "release_manifest.json", {"schema": "goalevolve.release-manifest.v1", "artifacts": artifacts})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
