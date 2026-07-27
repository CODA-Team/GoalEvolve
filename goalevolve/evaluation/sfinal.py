"""Observer-only implementation of the official reference Sfinal score.

Sfinal is retained for reporting against contest-oriented baselines.  It is
never returned as an evolution metric, so contracts, EPD, Teacher prompts, and
promotion cannot use it as a decision signal.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping

from ..core.io import atomic_json


OFFICIAL_EQUIV_CELLS = Path(__file__).resolve().parents[2] / "third_party" / "official_checker" / "validity_check" / "asap7_equivalent_cell_list.csv"
_WEIGHTS = {"tns": 30.0, "dynamic_power": 50.0, "leakage_power": 50.0, "slew": 0.001, "cap": 10.0, "fanout": 1.0, "tool_runtime": 1.0, "flow_runtime": 1.0, "displacement": 0.5, "max_overflow": 1.0, "total_overflow": 1.0}
_FLOAT_KEYS = frozenset({"wns", "tns", "slew_over_sum", "cap_over_sum", "fanout_over_sum", "leakage_power", "total_power", "max_gr_overflow", "total_gr_overflow", "tool_runtime", "flow_runtime"})


def _number(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _as_float(value: str | None) -> float | None:
    return None if value is None or not value.strip() else float(value)


def _safe_norm_delta(current: float | None, baseline: float | None, *, absolute_denominator: bool = False) -> float:
    current_value, baseline_value = _number(current), _number(baseline)
    denominator = abs(baseline_value) if absolute_denominator else baseline_value
    return (0.0 if current_value == 0.0 else current_value - baseline_value) if denominator == 0.0 else (current_value - baseline_value) / denominator


def _improvement_for_lower_value(current: float | None, baseline: float | None) -> float:
    current_value, baseline_value = _number(current), _number(baseline)
    return (0.0 if current_value == 0.0 else baseline_value - current_value) if baseline_value == 0.0 else (baseline_value - current_value) / baseline_value


def _read_metrics(path: Path, design: str) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if any((value or "").strip() for value in row.values())]
    if not rows:
        raise ValueError(f"no metric rows in {path}")
    row = next((item for item in reversed(rows) if str(item.get("design") or "").strip() == design), rows[-1])
    parsed: dict[str, Any] = dict(row)
    for key in _FLOAT_KEYS:
        parsed[key] = _as_float(row.get(key))
    return parsed


def _load_nodes(path: Path) -> dict[str, tuple[str, str, float, float]]:
    """Official 2026 node.csv reader, kept local to avoid a runtime package dependency."""
    with path.open(newline="", encoding="utf-8") as stream:
        rows = csv.reader(stream)
        next(rows, None)
        return {row[0]: (row[1], row[2], float(row[3]), float(row[4])) for row in rows if len(row) >= 5}


def _load_equivalent_cells(path: Path) -> dict[str, int]:
    groups: dict[str, int] = {}
    for group_id, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        for cell in (item.strip() for item in line.split(",")):
            if cell:
                groups[cell] = group_id
    return groups


def _average_logic_displacement(*, baseline: Mapping[str, tuple[str, str, float, float]], candidate: Mapping[str, tuple[str, str, float, float]], equivalent_cells: Mapping[str, int]) -> float:
    # This is calculate_logic_cell_movement from the official 2026 checker.
    total, count = 0.0, 0
    for name, (master, node_type, before_x, before_y) in baseline.items():
        if node_type != "Inst" or master not in equivalent_cells or name not in candidate:
            continue
        _after_master, _after_type, after_x, after_y = candidate[name]
        total += abs(after_x - before_x) + abs(after_y - before_y)
        count += 1
    return total / count if count else 0.0


def score_sfinal(*, baseline: Mapping[str, Any], candidate: Mapping[str, Any], average_displacement: float) -> dict[str, float]:
    """Return the official decomposition without making an evolution decision."""
    baseline_dynamic = _number(baseline.get("total_power")) - _number(baseline.get("leakage_power"))
    candidate_dynamic = _number(candidate.get("total_power")) - _number(candidate.get("leakage_power"))
    tns_norm = _safe_norm_delta(candidate.get("tns"), baseline.get("tns"), absolute_denominator=True)
    dynamic_power_norm = _improvement_for_lower_value(candidate_dynamic, baseline_dynamic)
    leakage_power_norm = _improvement_for_lower_value(candidate.get("leakage_power"), baseline.get("leakage_power"))
    sppa = _WEIGHTS["tns"] * tns_norm + _WEIGHTS["dynamic_power"] * dynamic_power_norm + _WEIGHTS["leakage_power"] * leakage_power_norm
    slew_norm = _safe_norm_delta(candidate.get("slew_over_sum"), baseline.get("slew_over_sum"))
    cap_norm = _safe_norm_delta(candidate.get("cap_over_sum"), baseline.get("cap_over_sum"))
    fanout_norm = _safe_norm_delta(candidate.get("fanout_over_sum"), baseline.get("fanout_over_sum"))
    perc = _WEIGHTS["slew"] * slew_norm + _WEIGHTS["cap"] * cap_norm + _WEIGHTS["fanout"] * fanout_norm
    runtime_tool = _safe_norm_delta(candidate.get("tool_runtime"), baseline.get("tool_runtime"))
    runtime_flow = _safe_norm_delta(candidate.get("flow_runtime"), baseline.get("flow_runtime"))
    runtime_penalty = _WEIGHTS["tool_runtime"] * runtime_tool + _WEIGHTS["flow_runtime"] * runtime_flow
    maximum_overflow = max(0.0, _number(candidate.get("max_gr_overflow")))
    total_overflow = max(0.0, _number(candidate.get("total_gr_overflow")))
    overflow_penalty = _WEIGHTS["max_overflow"] * maximum_overflow + _WEIGHTS["total_overflow"] * total_overflow
    displacement_penalty = _WEIGHTS["displacement"] * float(average_displacement)
    return {"TNS_norm": tns_norm, "DPOWER_norm": dynamic_power_norm, "LPOWER_norm": leakage_power_norm, "SPPA": sppa, "SLEW_norm": slew_norm, "CAP_norm": cap_norm, "FANOUT_norm": fanout_norm, "PERC": perc, "Rtool": runtime_tool, "Rflow": runtime_flow, "R": runtime_penalty, "Davg": float(average_displacement), "Pmax": maximum_overflow, "Ptotal": total_overflow, "Poverflow": overflow_penalty, "Sfinal": sppa - perc - runtime_penalty - displacement_penalty - overflow_penalty}


def observe_sfinal(*, design: str, benchmark_dir: Path, candidate_dir: Path, output: Path | None = None, equivalent_cells: Path = OFFICIAL_EQUIV_CELLS) -> dict[str, object]:
    """Calculate and persist an observer-only official Sfinal report."""
    baseline_metrics, candidate_metrics = benchmark_dir / "metrics.csv", candidate_dir / "metrics.csv"
    for required in (baseline_metrics, candidate_metrics, benchmark_dir / "node.csv", candidate_dir / "node.csv", equivalent_cells):
        if not required.is_file():
            raise FileNotFoundError(f"Sfinal observation prerequisite missing: {required}")
    pre_nodes, post_nodes = _load_nodes(benchmark_dir / "node.csv"), _load_nodes(candidate_dir / "node.csv")
    average_displacement = _average_logic_displacement(baseline=pre_nodes, candidate=post_nodes, equivalent_cells=_load_equivalent_cells(equivalent_cells))
    report: dict[str, object] = {
        "schema_version": "goalevolve.v2.sfinal_observation.v1",
        "decision_role": "observer_only",
        "design": design,
        "formula_source": "reference official evaluation/compute_score.py",
        "baseline_metrics": str(baseline_metrics),
        "candidate_metrics": str(candidate_metrics),
        "baseline_post_opt": str(benchmark_dir),
        "candidate_post_opt": str(candidate_dir),
        "equivalent_cells": str(equivalent_cells),
        "score": score_sfinal(baseline=_read_metrics(baseline_metrics, design), candidate=_read_metrics(candidate_metrics, design), average_displacement=float(average_displacement)),
    }
    atomic_json(output or candidate_dir / "sfinal_observation.json", report)
    return report
