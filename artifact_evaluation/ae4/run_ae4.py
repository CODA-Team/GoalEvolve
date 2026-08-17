#!/usr/bin/env python3
"""Prepare, run, and summarize the isolated AE4 transfer experiment."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

AE4_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = AE4_ROOT.parent
CONFIG_PATH = AE4_ROOT / "experiment.json"
RESULTS_ROOT = AE4_ROOT / "results"


BASELINE_FLOW = [
    "repair_design",
    "repair_timing -setup",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def project_path(value: str | Path) -> Path:
    """Resolve a release path without encoding a maintainer's machine path."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def benchmark_root(config: dict) -> Path:
    return project_path(config["benchmark_root"])


def source_binary_path(config: dict) -> Path:
    return project_path(config["source_binary"]["path"])


def schedule_for(schedule: str) -> list[str]:
    if schedule != "baseline_flow":
        raise ValueError(f"unknown schedule: {schedule}")
    return BASELINE_FLOW


def tcl_for(config: dict, design: str, schedule: str, run_root: Path) -> str:
    inputs_root = benchmark_root(config)
    design_root = inputs_root / "benchmarks" / design
    asap7 = inputs_root / "asap7"
    steps = schedule_for(schedule)
    lines = [
        f"# AE4 design={design} schedule={schedule}",
        f"# AES AE2 artifact={config['source_binary']['ae2_artifact']}",
        "set ae4_start [clock seconds]",
        f"set ae4_out {{{run_root}}}",
        f"foreach lef [lsort [glob -nocomplain {{{asap7 / 'lef'}/*.lef}}]] {{ read_lef $lef }}",
        f"foreach lib [lsort [glob -nocomplain {{{asap7 / 'lib'}/*.lib}}]] {{ read_liberty $lib }}",
        f"read_def {{{design_root / (design + '.def.gz')}}}",
        f"read_verilog {{{design_root / (design + '.v')}}}",
        f"read_sdc {{{design_root / (design + '.sdc')}}}",
        "set_ideal_network [all_clocks]",
        f"source {{{asap7 / 'setRC.tcl'}}}",
        "set_cmd_units -time ns -capacitance pF -current mA -voltage V -resistance kOhm -distance um -power mW",
        "set_units -power mW",
        "estimate_parasitics -placement",
        'puts "AE4_SCHEDULE_BEGIN"',
        *steps,
        'puts "AE4_SCHEDULE_END"',
        "set_placement_padding -global -left 0 -right 0",
        "detailed_placement",
        "improve_placement -max_displacement {5 1}",
        "optimize_mirroring",
        "check_placement -verbose",
        "estimate_parasitics -placement",
        "set_power_activity -global -activity 0.1 -duty 0.5",
        "unset_power_activity -global",
        'puts "AE4_METRICS_BEGIN post_placement"',
        'puts [format "AE4_METRIC post_placement tns_ns %.12g" [total_negative_slack -max]]',
        'puts [format "AE4_METRIC post_placement wns_ns %.12g" [worst_slack -max]]',
        "report_power -digits 12",
        'puts "AE4_METRICS_END post_placement"',
        "if {[info exists route_signal_layers]} { set signal_layers $route_signal_layers } else { set signal_layers M2-M9 }",
        "if {[info exists route_clock_layers]} { set clock_layers $route_clock_layers } else { set clock_layers M2-M9 }",
        "set_routing_layers -signal $signal_layers -clock $clock_layers",
        "global_route -skip_large_fanout_nets 300 -allow_congestion -congestion_iterations 50",
        "estimate_parasitics -global_routing",
        "set_power_activity -global -activity 0.1 -duty 0.5",
        "unset_power_activity -global",
        'puts "AE4_METRICS_BEGIN post_route"',
        'puts [format "AE4_METRIC post_route tns_ns %.12g" [total_negative_slack -max]]',
        'puts [format "AE4_METRIC post_route wns_ns %.12g" [worst_slack -max]]',
        "report_power -digits 12",
        'puts "AE4_METRICS_END post_route"',
        'puts [format "AE4_RUNTIME_S %d" [expr {[clock seconds] - $ae4_start}]]',
        "exit",
    ]
    return "\n".join(lines) + "\n"


def validate_inputs(config: dict) -> dict:
    """Validate the released AE2 prerequisite for the fixed baseline flow.

    An AE4 binary is intentionally not checksum-pinned: rebuilding the exact
    frozen source on another host changes ELF bytes.  Instead an AE2 report
    proves that this local executable passed the selected AES source hash,
    captured Tcl, QoR comparison, and official 4/4 check.
    """
    binary = source_binary_path(config)
    report_path = project_path(config["source_binary"]["report"])
    if not os.access(binary, os.X_OK):
        raise FileNotFoundError(
            "AES AE2 executable is missing. Run the README AE2 command for "
            f"{config['source_binary']['ae2_artifact']} first: {binary}"
        )
    if not report_path.is_file():
        raise FileNotFoundError(
            "AES AE2 report is missing. AE4 requires a completed, passing AE2 replay: "
            f"{report_path}"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("passed"):
        raise RuntimeError("AES AE2 report is not passing; do not run AE4 from an unverified binary")
    if report.get("artifact_id") != config["source_binary"]["ae2_artifact"]:
        raise RuntimeError("AES AE2 report belongs to a different artifact")
    if report.get("source_hash") != config["source_binary"]["source_hash"]:
        raise RuntimeError("AES AE2 report source hash does not match the AE4 frozen source")
    release_manifest = json.loads(
        (PROJECT_ROOT / "artifact_evaluation" / "release_manifest.json").read_text(encoding="utf-8")
    )
    artifact = dict(release_manifest.get("artifacts", {}).get(report["artifact_id"]) or {})
    expected_tcl = project_path(str(artifact.get("expected_root") or "")) / "evaluate.tcl"
    if not expected_tcl.is_file():
        raise RuntimeError("AES AE2 release manifest has no portable evaluation Tcl")
    if report.get("portable_tcl_sha256") != sha256(expected_tcl):
        raise RuntimeError(
            "AES AE2 report was produced by an older portable Tcl; rerun the README AE2 command first"
        )

    inputs_root = benchmark_root(config)
    provenance = {
        "source_binary": {
            **config["source_binary"],
            "resolved_path": str(binary),
            "observed_sha256": sha256(binary),
            "ae2_report_sha256": sha256(report_path),
            "portable_tcl_sha256": sha256(expected_tcl),
        },
        "table1_baseline_sources": {},
    }
    for design, entry in config["designs"].items():
        for suffix in ("def.gz", "v", "sdc"):
            path = inputs_root / "benchmarks" / design / f"{design}.{suffix}"
            if not path.is_file():
                raise FileNotFoundError(path)
        baseline_source = inputs_root / entry["baseline_source"]
        provenance["table1_baseline_sources"][design] = {
            "path": str(baseline_source),
            "sha256": sha256(baseline_source),
        }
    return provenance


def prepare(config: dict) -> list[tuple[str, str, Path]]:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    provenance = validate_inputs(config)
    atomic_json(RESULTS_ROOT / "provenance.json", provenance)
    runs = []
    for design in config["designs"]:
        for schedule in config["schedule_order"]:
            run_root = RESULTS_ROOT / design / schedule
            run_root.mkdir(parents=True, exist_ok=True)
            tcl = run_root / "evaluate.tcl"
            tcl.write_text(tcl_for(config, design, schedule, run_root), encoding="utf-8")
            atomic_json(
                run_root / "run_manifest.json",
                {
                    "design": design,
                    "schedule": schedule,
                    "schedule_commands": schedule_for(schedule),
                    "tcl": str(tcl),
                    "tcl_sha256": sha256(tcl),
                    "binary": config["source_binary"],
                    "table1_baseline": config["designs"][design]["table1_baseline"],
                },
            )
            runs.append((design, schedule, run_root))
    return runs


def parse_stage(log_text: str, stage: str) -> dict | None:
    block_match = re.search(
        rf"AE4_METRICS_BEGIN {re.escape(stage)}(?P<body>.*?)AE4_METRICS_END {re.escape(stage)}",
        log_text,
        flags=re.DOTALL,
    )
    if not block_match:
        return None
    body = block_match.group("body")
    tns_match = re.search(rf"AE4_METRIC {re.escape(stage)} tns_ns ([^\s]+)", body)
    wns_match = re.search(rf"AE4_METRIC {re.escape(stage)} wns_ns ([^\s]+)", body)
    total_matches = re.findall(
        r"^Total\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)",
        body,
        flags=re.MULTILINE,
    )
    if not (tns_match and wns_match and total_matches):
        return None
    internal_w, switching_w, leakage_w, total_w = map(float, total_matches[-1])
    return {
        "tns_ns": float(tns_match.group(1)),
        "wns_ns": float(wns_match.group(1)),
        "internal_w": internal_w,
        "switching_w": switching_w,
        "leakage_uw": leakage_w * 1.0e6,
        "dynamic_uw": (internal_w + switching_w) * 1.0e6,
        "total_uw": total_w * 1.0e6,
    }


def parse_run(run_root: Path) -> dict:
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    log_path = run_root / "evaluation.log"
    status_path = run_root / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    placement = parse_stage(log_text, "post_placement")
    route = parse_stage(log_text, "post_route")
    runtime_match = re.search(r"AE4_RUNTIME_S (\d+)", log_text)
    placement_failures = re.findall(r"Total Placement Failures:\s+(\d+)", log_text)
    return {
        "design": manifest["design"],
        "schedule": manifest["schedule"],
        "ok": status.get("returncode") == 0 and route is not None,
        "returncode": status.get("returncode"),
        "wall_runtime_s": status.get("wall_runtime_s"),
        "flow_runtime_s": int(runtime_match.group(1)) if runtime_match else None,
        "placement_failures": int(placement_failures[-1]) if placement_failures else None,
        "post_placement": placement,
        "post_route": route,
        "log": str(log_path),
        "tcl": manifest["tcl"],
        "table1_baseline": manifest["table1_baseline"],
    }


def execute_one(config: dict, item: tuple[str, str, Path], force: bool) -> dict:
    design, schedule, run_root = item
    result_path = run_root / "result.json"
    if not force and result_path.is_file():
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        if existing.get("ok"):
            print(f"SKIP {design}/{schedule}: complete", flush=True)
            return existing
    binary = str(source_binary_path(config))
    tcl = run_root / "evaluate.tcl"
    log = run_root / "evaluation.log"
    print(f"START {design}/{schedule}", flush=True)
    start = time.monotonic()
    environment = os.environ.copy()
    environment.setdefault("OMP_NUM_THREADS", "4")
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.run(
            [binary, "-no_init", "-no_splash", "-exit", str(tcl)],
            cwd=run_root,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    atomic_json(
        run_root / "status.json",
        {
            "returncode": process.returncode,
            "wall_runtime_s": time.monotonic() - start,
        },
    )
    parsed = parse_run(run_root)
    atomic_json(result_path, parsed)
    print(
        f"DONE {design}/{schedule}: ok={parsed['ok']} rc={process.returncode} "
        f"wall={parsed['wall_runtime_s']:.1f}s",
        flush=True,
    )
    return parsed


def percent_improvement(metric: str, baseline: float, value: float) -> float:
    if metric == "tns_ns":
        return (value - baseline) / abs(baseline) * 100.0
    return (baseline - value) / baseline * 100.0


def absolute_improvement(metric: str, baseline: float, value: float) -> float:
    if metric == "tns_ns":
        return value - baseline
    return baseline - value


def schedule_label(schedule: str) -> str:
    if schedule != "baseline_flow":
        raise ValueError(f"unknown schedule: {schedule}")
    return "Baseline flow"


def aggregate_rows(config: dict, rows: list[dict]) -> list[dict]:
    by_schedule: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["ok"]:
            by_schedule[row["schedule"]].append(row)

    aggregates = []
    metrics = ("tns_ns", "leakage_uw", "dynamic_uw")
    for schedule in config["schedule_order"]:
        selected = by_schedule[schedule]
        metric_wins = {
            metric: sum(row[f"{metric}_improved"] for row in selected)
            for metric in metrics
        }
        macro = {
            metric: (
                sum(row[f"{metric}_improvement_pct"] for row in selected) / len(selected)
                if selected
                else None
            )
            for metric in metrics
        }
        weighted = {}
        ratios = []
        for metric in metrics:
            denominator = 0.0
            numerator = 0.0
            for row in selected:
                baseline = config["designs"][row["design"]]["table1_baseline"][metric]
                value = row[metric]
                if metric == "tns_ns":
                    baseline = abs(baseline)
                    value = abs(value)
                denominator += baseline
                numerator += value
                if baseline > 0.0 and value > 0.0:
                    ratios.append(value / baseline)
            weighted[metric] = (
                (denominator - numerator) / denominator * 100.0
                if denominator > 0.0
                else None
            )
        geometric_gain = (
            (1.0 - math.exp(sum(math.log(ratio) for ratio in ratios) / len(ratios))) * 100.0
            if ratios
            else None
        )
        aggregates.append(
            {
                "schedule": schedule,
                "successful_designs": len(selected),
                "all_three_improved_designs": sum(row["all_three_improved"] for row in selected),
                "metric_wins": sum(metric_wins.values()),
                "metric_opportunities": len(selected) * len(metrics),
                "wins_by_metric": metric_wins,
                "macro_mean_improvement_pct": macro,
                "pooled_improvement_pct": weighted,
                "equal_weight_geometric_gain_pct": geometric_gain,
            }
        )
    return aggregates


def write_reports(config: dict, rows: list[dict]) -> None:
    aggregates = aggregate_rows(config, rows)
    atomic_json(RESULTS_ROOT / "aggregate.json", aggregates)

    lines = [
        "# AE4 cross-design transfer results",
        "",
        "All rows use the AES AE2 r58 OpenROAD executable. Improvements are measured against",
        "the corresponding contest/Table-1 baseline, not against AE4 `baseline_flow`.",
            "Positive deltas and percentages mean better QoR. Dynamic power is internal plus switching.",
            "Runtime is observer telemetry only and is excluded from every win count and aggregate QoR statistic.",
        "",
        "## Table-1 reference baselines",
        "",
        "| Design | TNS (ns) | Leakage (uW) | Dynamic (uW) |",
        "|---|---:|---:|---:|",
    ]
    for design, entry in config["designs"].items():
        baseline = entry["table1_baseline"]
        lines.append(
            f"| {design} | {baseline['tns_ns']:.3f} | {baseline['leakage_uw']:,.3f} | "
            f"{baseline['dynamic_uw']:,.3f} |"
        )

    lines.extend(
        [
            "",
            "## Post-route QoR and improvement over Table 1",
            "",
            "| Design | Schedule | TNS (ns) | TNS gain ns (%) | Leakage (uW) | Leakage saved uW (%) | Dynamic (uW) | Dynamic saved uW (%) | All 3 better? |",
            "|---|---|---:|---:|---:|---:|---:|---:|:---:|",
        ]
    )
    for row in rows:
        if not row["ok"]:
            lines.append(
                f"| {row['design']} | {schedule_label(row['schedule'])} | -- | -- | -- | -- | -- | -- | no result |"
            )
            continue
        lines.append(
            f"| {row['design']} | {schedule_label(row['schedule'])} | {row['tns_ns']:.3f} | "
            f"{row['tns_ns_improvement_abs']:+.3f} ({row['tns_ns_improvement_pct']:+.2f}%) | "
            f"{row['leakage_uw']:,.3f} | {row['leakage_uw_improvement_abs']:+,.3f} "
            f"({row['leakage_uw_improvement_pct']:+.2f}%) | {row['dynamic_uw']:,.3f} | "
            f"{row['dynamic_uw_improvement_abs']:+,.3f} ({row['dynamic_uw_improvement_pct']:+.2f}%) | "
            f"{'yes' if row['all_three_improved'] else 'no'} |"
        )

    lines.extend(
        [
            "",
            "## Aggregate transfer evidence",
            "",
            "| Schedule | Complete | Designs with all 3 better | Metric wins | TNS wins | Leakage wins | Dynamic wins | Equal-weight geometric gain |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for aggregate in aggregates:
        wins = aggregate["wins_by_metric"]
        gain = aggregate["equal_weight_geometric_gain_pct"]
        lines.append(
            f"| {schedule_label(aggregate['schedule'])} | {aggregate['successful_designs']}/7 | "
            f"{aggregate['all_three_improved_designs']}/7 | "
            f"{aggregate['metric_wins']}/{aggregate['metric_opportunities']} | "
            f"{wins['tns_ns']}/{aggregate['successful_designs']} | "
            f"{wins['leakage_uw']}/{aggregate['successful_designs']} | "
            f"{wins['dynamic_uw']}/{aggregate['successful_designs']} | "
            f"{gain:+.2f}% |" if gain is not None else
            f"| {schedule_label(aggregate['schedule'])} | 0/7 | 0/7 | 0/0 | 0/0 | 0/0 | 0/0 | -- |"
        )

    if all(aggregate["successful_designs"] == 7 for aggregate in aggregates):
        lines.extend(
            [
                "",
                "## Interpretation",
                "",
                "Every result uses `baseline_flow`: no AES-specific or target-design-specific",
                "optimization policy is injected, so the comparison isolates the AES-evolved",
                "OpenROAD source under a stock schedule.",
            ]
        )
    (RESULTS_ROOT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    latex = [
        r"\begin{table*}[t]",
        r"\centering",
        r"\footnotesize",
        r"\caption{AE4 cross-design transfer using the AES-evolved OpenROAD. Parentheses show improvement over the contest/Table-1 baseline; positive is better.}",
        r"\label{tab:ae4-transfer}",
        r"\begin{tabular}{llrrr}",
        r"\toprule",
        r"Design & Schedule & TNS (ns) & $P_{\mathrm{leak}}$ ($\mu$W) & $P_{\mathrm{{dyn}}}$ ($\mu$W) \\",
        r"\midrule",
    ]
    for design in config["designs"]:
        design_rows = [row for row in rows if row["design"] == design]
        for index, row in enumerate(design_rows):
            label = design.replace("_", r"\_") if index == 0 else ""
            if row["ok"]:
                latex.append(
                    f"{label} & {schedule_label(row['schedule'])} & "
                    f"{row['tns_ns']:.2f} ({row['tns_ns_improvement_pct']:+.1f}\\%) & "
                    f"{row['leakage_uw']:.1f} ({row['leakage_uw_improvement_pct']:+.1f}\\%) & "
                    f"{row['dynamic_uw']:.1f} ({row['dynamic_uw_improvement_pct']:+.1f}\\%) \\\\"
                )
            else:
                latex.append(f"{label} & {schedule_label(row['schedule'])} & -- & -- & -- \\\\")
        latex.append(r"\addlinespace")
    latex.extend([r"\bottomrule", r"\end{tabular}", r"\end{table*}"])
    (RESULTS_ROOT / "table.tex").write_text("\n".join(latex) + "\n", encoding="utf-8")


def collect(config: dict) -> list[dict]:
    rows = []
    for design in config["designs"]:
        for schedule in config["schedule_order"]:
            run_root = RESULTS_ROOT / design / schedule
            if not (run_root / "run_manifest.json").is_file():
                continue
            parsed = parse_run(run_root)
            atomic_json(run_root / "result.json", parsed)
            route = parsed["post_route"] or {}
            baseline = parsed["table1_baseline"]
            row = {
                "design": design,
                "schedule": schedule,
                "ok": parsed["ok"],
                "tns_ns": route.get("tns_ns"),
                "leakage_uw": route.get("leakage_uw"),
                "dynamic_uw": route.get("dynamic_uw"),
                "placement_failures": parsed["placement_failures"],
                "flow_runtime_s": parsed["flow_runtime_s"],
            }
            for metric in ("tns_ns", "leakage_uw", "dynamic_uw"):
                value = row[metric]
                row[f"{metric}_improvement_pct"] = (
                    percent_improvement(metric, baseline[metric], value)
                    if value is not None
                    else None
                )
                row[f"{metric}_improvement_abs"] = (
                    absolute_improvement(metric, baseline[metric], value)
                    if value is not None
                    else None
                )
                row[f"{metric}_improved"] = (
                    row[f"{metric}_improvement_pct"] > 0.0
                    if value is not None
                    else False
                )
            row["all_three_improved"] = all(
                row[f"{metric}_improved"]
                for metric in ("tns_ns", "leakage_uw", "dynamic_uw")
            )
            rows.append(row)
    atomic_json(RESULTS_ROOT / "summary.json", rows)
    fieldnames = list(rows[0]) if rows else []
    if rows:
        with (RESULTS_ROOT / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    write_reports(config, rows)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--jobs", type=int, default=7)
    run_parser.add_argument("--force", action="store_true")
    subparsers.add_parser("collect")
    arguments = parser.parse_args()
    config = load_config()
    runs = prepare(config)
    if arguments.command == "prepare":
        print(f"Prepared {len(runs)} runs under {RESULTS_ROOT}")
        return 0
    if arguments.command == "run":
        with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.jobs) as executor:
            futures = [executor.submit(execute_one, config, item, arguments.force) for item in runs]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        rows = collect(config)
        complete = sum(bool(row["ok"]) for row in rows)
        print(f"Collected {complete}/{len(runs)} successful runs")
        return 0 if complete == len(runs) else 1
    rows = collect(config)
    complete = sum(bool(row["ok"]) for row in rows)
    print(f"Collected {complete}/{len(rows)} successful runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
