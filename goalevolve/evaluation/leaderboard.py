"""Cross-design leaderboard built from verified GoalEvolve evaluations.

The leaderboard is reporting state, not an evolution input. Ranking is
performed independently for each frozen design/contract pair with the exact
GoalContract distance formula. Runtime, SPPA, and Sfinal are observer columns.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core.contracts import GoalContract
from ..core.io import atomic_json, load_json


REQUIRED_CHECKS = ("build", "flow", "metrics", "lec")
CSV_FIELDS = (
    "design_rank", "design", "contract_id", "campaign", "result_id",
    "round", "student", "goal_distance", "tns_abs_ns", "tns_target_ns",
    "tns_gap_ns", "tns_normalized_gap", "dynamic_power_pw",
    "dynamic_target_pw", "dynamic_gap_pw", "dynamic_normalized_gap",
    "leakage_power_pw", "leakage_target_pw", "leakage_gap_pw",
    "leakage_normalized_gap", "drv_count", "official_checks",
    "evidence_state", "lineage_status", "timing_recipe_id", "sppa",
    "sfinal", "runtime_s", "source_commit", "artifact_dir",
    "tcl_file", "tcl_sha256", "execution_log",
)


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _round_number(candidate_path: Path) -> int:
    for parent in candidate_path.parents:
        if parent.name.startswith("round_"):
            suffix = parent.name.removeprefix("round_")
            if suffix.isdigit():
                return int(suffix)
    return -1


def _checks_pass(candidate: Mapping[str, Any]) -> bool:
    checks = {
        str(item.get("name") or ""): bool(item.get("passed"))
        for item in list(candidate.get("checks") or [])
        if isinstance(item, Mapping)
    }
    return all(checks.get(name, False) for name in REQUIRED_CHECKS)


def _lineage_ids(state_root: Path) -> tuple[set[str], str]:
    promoted: set[str] = set()
    current = str(dict(load_json(state_root / "parent.json", {}) or {}).get("parent_id") or "")
    for summary_path in sorted((state_root / "rounds").glob("round_*/round.json")):
        if not summary_path.parent.name.removeprefix("round_").isdigit():
            continue
        summary = load_json(summary_path, {}) or {}
        parent_id = str(dict(summary.get("parent_after") or {}).get("parent_id") or "")
        if parent_id.startswith("round_"):
            promoted.add(parent_id)
    return promoted, current


def _observer_score(artifact_dir: Path, candidate: Mapping[str, Any]) -> tuple[float | None, float | None]:
    paths = [artifact_dir / "contest_output" / "sfinal_observation.json"]
    configured = dict(candidate.get("artifacts") or {}).get("sfinal_observation")
    if configured:
        paths.insert(0, Path(str(configured)))
    for path in paths:
        report = load_json(path, {}) or {}
        score = dict(report.get("score") or {})
        if score:
            return _number(score.get("SPPA")), _number(score.get("Sfinal"))
    return None, None


def _execution_record(artifact_dir: Path, candidate: Mapping[str, Any]) -> tuple[Path | None, str | None, Path | None]:
    """Locate and fingerprint the exact Tcl and OpenROAD log used by a result."""
    artifacts = dict(candidate.get("artifacts") or {})
    result_root_raw = artifacts.get("result_root")
    result_root = Path(str(result_root_raw)).resolve() if result_root_raw else artifact_dir

    def locate(*, keys: tuple[str, ...], relative_paths: tuple[Path, ...]) -> Path | None:
        for key in keys:
            configured = artifacts.get(key)
            if not configured:
                continue
            path = Path(str(configured))
            path = path if path.is_absolute() else artifact_dir / path
            if path.is_file():
                return path.resolve()
        for root in (artifact_dir, result_root):
            for relative in relative_paths:
                path = root / relative
                if path.is_file():
                    return path.resolve()
        return None

    tcl = locate(
        keys=("evaluation_tcl", "evaluate_tcl", "tcl_file"),
        relative_paths=(Path("contest_output/evaluate.tcl"), Path("evaluate.tcl")),
    )
    log = locate(
        keys=("evaluation_log", "execution_log"),
        relative_paths=(Path("contest_output/evaluation.log"), Path("evaluation.log")),
    )
    digest = hashlib.sha256(tcl.read_bytes()).hexdigest() if tcl is not None else None
    return tcl, digest, log


def _metric_columns(contract: GoalContract, metrics: Mapping[str, Any]) -> dict[str, float | None]:
    specs = {spec.name: spec for spec in contract.metrics}
    result: dict[str, float | None] = {}
    for name, prefix in (
        ("tns_abs_ns", "tns"),
        ("dynamic_power_pw", "dynamic"),
        ("leakage_power_pw", "leakage"),
    ):
        value = _number(metrics.get(name))
        spec = specs.get(name)
        target = None if spec is None else float(spec.target)
        result[f"{prefix}_value"] = value
        result[f"{prefix}_target"] = target
        result[f"{prefix}_gap"] = None if value is None or target is None else max(0.0, value - target)
        result[f"{prefix}_normalized_gap"] = None if spec is None else spec.residual(value)
    return result


def collect_campaign_results(state_root: Path) -> list[dict[str, Any]]:
    """Collect integrity-valid, zero-DRV results from one frozen campaign."""
    state_root = state_root.resolve()
    contract_data = load_json(state_root / "contract.json")
    if not isinstance(contract_data, Mapping):
        return []
    contract = GoalContract.from_dict(contract_data)
    promoted_ids, current_id = _lineage_ids(state_root)
    rows: list[dict[str, Any]] = []
    pattern = "round_*/students/*/artifacts/candidate.json"
    for candidate_path in sorted((state_root / "rounds").glob(pattern)):
        candidate = load_json(candidate_path, {}) or {}
        metrics = dict(candidate.get("metrics") or {})
        drv = _number(metrics.get("drv_count"))
        if candidate.get("evaluation_error") or not _checks_pass(candidate) or drv != 0.0:
            continue
        distance, residuals, missing = contract.evaluate(metrics)
        if missing:
            continue
        round_index = _round_number(candidate_path)
        if round_index < 0:
            continue
        student = str(candidate.get("student_id") or candidate_path.parents[1].name)
        result_id = f"round_{round_index:03d}:{student}"
        evidence = load_json(candidate_path.with_name("evidence.json"), {}) or {}
        artifact_dir = candidate_path.parent
        tcl_file, tcl_sha256, execution_log = _execution_record(artifact_dir, candidate)
        # A leaderboard row must be independently replayable and auditable,
        # not merely a detached QoR tuple.
        if tcl_file is None or execution_log is None:
            continue
        sppa, sfinal = _observer_score(artifact_dir, candidate)
        lineage_status = "current_parent" if result_id == current_id else "promoted" if result_id in promoted_ids else "not_promoted"
        columns = _metric_columns(contract, metrics)
        rows.append({
            "design": contract.design,
            "contract_id": contract.contract_id,
            "campaign": state_root.name,
            "result_id": result_id,
            "round": round_index,
            "student": student,
            "goal_distance": distance,
            "metric_residuals": residuals,
            "tns_abs_ns": columns["tns_value"],
            "tns_target_ns": columns["tns_target"],
            "tns_gap_ns": columns["tns_gap"],
            "tns_normalized_gap": columns["tns_normalized_gap"],
            "dynamic_power_pw": columns["dynamic_value"],
            "dynamic_target_pw": columns["dynamic_target"],
            "dynamic_gap_pw": columns["dynamic_gap"],
            "dynamic_normalized_gap": columns["dynamic_normalized_gap"],
            "leakage_power_pw": columns["leakage_value"],
            "leakage_target_pw": columns["leakage_target"],
            "leakage_gap_pw": columns["leakage_gap"],
            "leakage_normalized_gap": columns["leakage_normalized_gap"],
            "drv_count": drv,
            "official_checks": "4/4",
            "evidence_state": str(evidence.get("state") or "unclassified"),
            "lineage_status": lineage_status,
            "timing_recipe_id": str(dict(candidate.get("hypothesis") or {}).get("timing_recipe_id") or ""),
            "sppa": sppa,
            "sfinal": sfinal,
            "runtime_s": _number(metrics.get("runtime_s")),
            "source_commit": str(candidate.get("source_commit") or ""),
            "artifact_dir": str(dict(candidate.get("artifacts") or {}).get("result_root") or artifact_dir),
            "tcl_file": str(tcl_file),
            "tcl_sha256": tcl_sha256,
            "execution_log": str(execution_log),
        })
    return rows


def _deduplicate(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one representative when replayed QoR is exactly identical."""
    evidence_priority = {"validated": 0, "verified_qor_unattributed": 1, "refuted": 2, "unclassified": 3}
    lineage_priority = {"current_parent": 0, "promoted": 1, "not_promoted": 2}
    chosen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (row["design"], row["contract_id"], row["tns_abs_ns"], row["dynamic_power_pw"], row["leakage_power_pw"])
        priority = (lineage_priority.get(str(row["lineage_status"]), 9), evidence_priority.get(str(row["evidence_state"]), 9), int(row["round"]), str(row["student"]))
        old = chosen.get(key)
        if old is None:
            chosen[key] = row
            continue
        old_priority = (lineage_priority.get(str(old["lineage_status"]), 9), evidence_priority.get(str(old["evidence_state"]), 9), int(old["round"]), str(old["student"]))
        if priority < old_priority:
            chosen[key] = row
    return list(chosen.values())


def rank_top_results(rows: Iterable[dict[str, Any]], *, top_k: int = 5) -> list[dict[str, Any]]:
    """Rank Top-K independently per design and frozen contract."""
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in _deduplicate(rows):
        groups.setdefault((str(row["design"]), str(row["contract_id"])), []).append(row)
    ranked: list[dict[str, Any]] = []
    for group_key in sorted(groups):
        ordered = sorted(groups[group_key], key=lambda row: (
            float(row["goal_distance"]),
            float(row["tns_abs_ns"] if row["tns_abs_ns"] is not None else float("inf")),
            float(row["dynamic_power_pw"] if row["dynamic_power_pw"] is not None else float("inf")),
            float(row["leakage_power_pw"] if row["leakage_power_pw"] is not None else float("inf")),
            int(row["round"]),
        ))
        for rank, row in enumerate(ordered[:top_k], start=1):
            ranked.append({"design_rank": rank, **row})
    return ranked


def _csv_text(rows: list[dict[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _display(value: Any, digits: int = 6) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}g}" if isinstance(value, float) else str(value)


def _markdown_link(label: str, path: Any) -> str:
    if not path:
        return "—"
    return f"[{label}](<{path}>)"


def _markdown_text(report: Mapping[str, Any]) -> str:
    lines = [
        "# GoalEvolve unified Top-5 leaderboard", "",
        f"Generated: `{report['generated_at']}`", "",
        "Ranking is independent per design and frozen contract. Only complete build/flow/metrics/official-4-of-4 results with zero DRV and a retained Tcl/execution-log record are included. Goal distance uses only the contract metrics; runtime, SPPA, and Sfinal are observer-only.", "",
    ]
    current_group: tuple[str, str] | None = None
    for row in list(report.get("rows") or []):
        group = (str(row["design"]), str(row["contract_id"]))
        if group != current_group:
            if current_group is not None:
                lines.append("")
            lines.extend([
                f"## {group[0]} — {group[1]}", "",
                "| Rank | Result | Distance | TNS / target (ns) | Dynamic / target (B pW) | Leakage / target (M pW) | SPPA | Sfinal | Tcl execution record | Evidence | Lineage |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|",
            ])
            current_group = group
        lines.append(
            "| {rank} | {result} | {distance} | {tns} / {tns_target} | {dynamic} / {dynamic_target} | {leakage} / {leakage_target} | {sppa} | {sfinal} | {tcl} (`{tcl_hash}`)<br>{log} | {evidence} | {lineage} |".format(
                rank=row["design_rank"], result=row["result_id"], distance=_display(row["goal_distance"], 10),
                tns=_display(row["tns_abs_ns"]), tns_target=_display(row["tns_target_ns"]),
                dynamic=_display(None if row["dynamic_power_pw"] is None else row["dynamic_power_pw"] / 1e9),
                dynamic_target=_display(None if row["dynamic_target_pw"] is None else row["dynamic_target_pw"] / 1e9),
                leakage=_display(None if row["leakage_power_pw"] is None else row["leakage_power_pw"] / 1e6),
                leakage_target=_display(None if row["leakage_target_pw"] is None else row["leakage_target_pw"] / 1e6),
                sppa=_display(row["sppa"]), sfinal=_display(row["sfinal"]),
                tcl=_markdown_link("Tcl", row.get("tcl_file")),
                tcl_hash=str(row.get("tcl_sha256") or "missing")[:12],
                log=_markdown_link("log", row.get("execution_log")),
                evidence=row["evidence_state"], lineage=row["lineage_status"],
            )
        )
    if not report.get("rows"):
        lines.append("No eligible result has been recorded.")
    return "\n".join([*lines, ""])


def update_unified_leaderboard(*, leaderboard_root: Path, state_roots: Iterable[Path] = (), top_k: int = 5) -> dict[str, Any]:
    """Register campaigns, rescan their evidence, and atomically update reports."""
    leaderboard_root = leaderboard_root.resolve()
    registry_path = leaderboard_root / "campaigns.json"
    registry = load_json(registry_path, {}) or {}
    registered = {str(Path(path).resolve()) for path in list(registry.get("state_roots") or [])}
    registered.update(str(Path(path).resolve()) for path in state_roots)
    valid_roots = sorted(path for path in registered if (Path(path) / "contract.json").is_file())
    atomic_json(registry_path, {"schema_version": "goalevolve.v2.leaderboard_registry.v1", "state_roots": valid_roots})
    candidates: list[dict[str, Any]] = []
    for path in valid_roots:
        candidates.extend(collect_campaign_results(Path(path)))
    rows = rank_top_results(candidates, top_k=top_k)
    report = {
        "schema_version": "goalevolve.v2.leaderboard.v2",
        "decision_role": "observer_only",
        "ranking_scope": "top_k_per_design_and_contract",
        "ranking_metric": "frozen_goal_contract_distance",
        "top_k": top_k,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campaigns": valid_roots,
        "rows": rows,
    }
    atomic_json(leaderboard_root / "leaderboard_top5.json", report)
    _atomic_text(leaderboard_root / "leaderboard_top5.csv", _csv_text(rows))
    _atomic_text(leaderboard_root / "leaderboard_top5.md", _markdown_text(report))
    return report
