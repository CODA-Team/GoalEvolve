"""AE-2 replay and OpenROAD preflight entry point."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from artifact_evaluation._common import (
    MANIFEST_PATH,
    PROJECT_ROOT,
    _artifact,
    _json,
    _path,
    _run,
    _sha256,
    _snapshot_matches,
    _stage_source,
    _toolchain_environment,
)


def ae2_preflight(*, artifact: dict[str, Any], openroad: Path | None, verbose: bool) -> dict[str, Any]:
    """Validate the frozen source and a prepared host OpenROAD executable."""
    source = _path(str(artifact["source_root"]))
    expected_root = _path(str(artifact["expected_root"]))
    report = PROJECT_ROOT / "outputs" / "ae2" / str(artifact["artifact_id"]) / "report"
    report.mkdir(parents=True, exist_ok=True)
    environment = _toolchain_environment()
    configured = openroad or (Path(os.environ["OPENROAD_EXE"]) if os.environ.get("OPENROAD_EXE") else None)
    checks: dict[str, bool] = {
        "frozen_source": source.is_dir(),
        "frozen_source_manifest": _snapshot_matches(source=source, manifest_path=expected_root / "source_manifest.json"),
        "openroad_executable": configured is not None and configured.is_file() and os.access(configured, os.X_OK),
    }
    version_rc: int | None = None
    version_log = report / "preflight_openroad_version.log"
    if checks["openroad_executable"]:
        assert configured is not None
        print(f"[AE-2 preflight] Checking prepared OpenROAD. Log: {version_log}", flush=True)
        version_rc = _run([str(configured), "-version"], cwd=PROJECT_ROOT, env=environment, log=version_log, live=verbose)
    checks["openroad_runs"] = version_rc == 0
    result = {
        "schema": "goalevolve.artifact-ae2-preflight.v1",
        "artifact_id": artifact["artifact_id"],
        "source": str(source),
        "openroad": str(configured) if configured is not None else None,
        "version_log": str(version_log),
        "version_returncode": version_rc,
        "checks": checks,
    }
    result["passed"] = all(checks.values())
    (report / "ae2_preflight.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _build_openroad(*, source: Path, build: Path, jobs: int, report: Path, verbose: bool) -> Path:
    # OpenROAD's top-level CMake install layout puts the executable in
    # ``bin/openroad``.  Keep this path explicit: AE-2 must validate the
    # binary just built from the frozen artifact, rather than falling back to
    # a binary inherited from another campaign.
    binary = build / "bin" / "openroad"
    if binary.is_file():
        return binary
    if build.exists():
        shutil.rmtree(build)
    staged_source = _stage_source(source=source, workspace=build.parent / "source")
    configure = [
        "cmake",
        "-S",
        str(staged_source),
        "-B",
        str(build),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DENABLE_TESTS=OFF",
    ]
    print(f"[AE-2] Configuring OpenROAD. Log: {report / 'configure.log'}", flush=True)
    if _run(configure, cwd=PROJECT_ROOT, env=_toolchain_environment(), log=report / "configure.log", live=verbose) != 0:
        raise RuntimeError(f"CMake configure failed; see {report / 'configure.log'}")
    build_command = ["cmake", "--build", str(build), "--target", "openroad", "-j", str(jobs)]
    print(f"[AE-2] Building OpenROAD with {jobs} jobs. Log: {report / 'build.log'}", flush=True)
    if _run(build_command, cwd=PROJECT_ROOT, env=_toolchain_environment(), log=report / "build.log", live=verbose) != 0:
        raise RuntimeError(f"OpenROAD build failed; see {report / 'build.log'}")
    if not binary.is_file():
        raise RuntimeError(f"build completed without expected binary {binary}")
    return binary


def _materialize_rmp_liberty(*, tcl: Path, benchmark_root: Path, output: Path) -> Path | None:
    """Create RMP's ABC library when a portable Tcl declares that dependency.

    The recorded RMP candidates refer to an evaluation-local
    ``rmp_standard_cells.lib``.  Campaign evaluation creates that combined
    standard-cell Liberty before starting OpenROAD, whereas a standalone
    replay starts with an empty output directory.  Keep this runner-side
    preparation explicit: it is support data for the fixed Tcl, not a change
    to the candidate's optimization schedule.
    """
    if "rmp_standard_cells.lib" not in tcl.read_text(encoding="utf-8"):
        return None
    liberty_root = benchmark_root.parents[1] / "asap7" / "lib"
    lib_files = tuple(sorted(liberty_root.glob("*.lib")))
    if not lib_files:
        raise RuntimeError(f"RMP replay requires Liberty files under {liberty_root}")
    from goalevolve.evaluation.contest2026 import _write_rmp_combined_liberty

    return _write_rmp_combined_liberty(lib_files, output)


def ae2(*, artifact: dict[str, Any], openroad: Path | None, jobs: int, rebuild: bool, verbose: bool) -> dict[str, Any]:
    """Build/replay one fixed source artifact and compare its official evidence."""
    source = _path(str(artifact["source_root"]))
    expected_root = _path(str(artifact["expected_root"]))
    if not _snapshot_matches(source=source, manifest_path=expected_root / "source_manifest.json"):
        raise RuntimeError("frozen AE-2 source does not match its release manifest")
    benchmark_root = _path(str(artifact["benchmark_root"]))
    output = PROJECT_ROOT / "outputs" / "ae2" / str(artifact["artifact_id"]) / "contest_output"
    report = output.parent / "report"
    output.mkdir(parents=True, exist_ok=True)
    report.mkdir(parents=True, exist_ok=True)
    build_dir = output.parent / "build"
    if openroad is not None:
        raise RuntimeError(
            "AE-2 requires the per-artifact OpenROAD build; use --openroad only with the preflight mode, "
            "then invoke ae2 with --rebuild or its existing per-artifact build cache"
        )
    openroad = _build_openroad(source=source, build=build_dir, jobs=jobs, report=report, verbose=verbose) if rebuild else build_dir / "bin" / "openroad"
    openroad = openroad.resolve()
    if not openroad.is_file():
        raise RuntimeError("no per-artifact OpenROAD executable: use --rebuild with a prepared host toolchain")
    environment = _toolchain_environment()
    environment.update({
        "GOALEVOLVE_PROJECT_ROOT": str(PROJECT_ROOT),
        "GOALEVOLVE_BENCHMARK_ROOT": str(benchmark_root.parents[1]),
        "GOALEVOLVE_AE_OUTPUT": str(output),
    })
    tcl = expected_root / "evaluate.tcl"
    rmp_liberty = _materialize_rmp_liberty(
        tcl=tcl,
        benchmark_root=benchmark_root,
        output=output,
    )
    print(f"[AE-2] Running post-route flow. Log: {output / 'evaluation.log'}", flush=True)
    flow_rc = _run([str(openroad), "-exit", str(tcl)], cwd=output, env=environment, log=output / "evaluation.log", live=verbose)
    checkpoint_metrics: dict[str, Any] = {}
    if flow_rc == 0:
        from goalevolve.evaluation.contest2026 import _checkpoint_metrics

        checkpoint_metrics = _checkpoint_metrics(output / "evaluation.log")
        (output / "checkpoint_metrics.json").write_text(
            json.dumps(checkpoint_metrics, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    metrics_csv = output / "metrics.csv"
    if metrics_csv.exists():
        metrics_csv.unlink()
    parser = PROJECT_ROOT / "third_party/official_checker/evaluation/parse_log.py"
    if flow_rc == 0:
        print(f"[AE-2] Parsing metrics. Log: {report / 'parse.log'}", flush=True)
        parser_rc = _run(
            [sys.executable, str(parser), "--csv", str(metrics_csv), str(output / "evaluation.log")],
            cwd=output,
            env=environment,
            log=report / "parse.log",
            live=verbose,
        )
    else:
        parser_rc = -1
    official_rc = -1
    official_detail = "flow_failed"
    if flow_rc == 0 and parser_rc == 0:
        print(f"[AE-2] Running official 4/4 validity check. Log: {output / 'official_4of4.log'}", flush=True)
        from goalevolve.evaluation.contest2026 import official_four_check
        from goalevolve.execution.execution import ExecutionPolicy
        passed, official_detail = official_four_check(
            pre_opt=benchmark_root,
            post_opt=output,
            output_log=output / "official_4of4.log",
            policy=ExecutionPolicy(timeout_s=3600, retries=1),
            environment=environment,
        )
        official_rc = 0 if passed else 1
    expected = dict(_json(expected_root / "candidate.json").get("metrics") or {})
    observed: dict[str, Any] = {}
    if metrics_csv.is_file():
        from goalevolve.evaluation.contest2026 import _read_metrics
        observed = _read_metrics(metrics_csv)
    tolerances = dict(artifact.get("tolerances") or {})
    comparisons: dict[str, dict[str, Any]] = {}
    for metric in ("tns_abs_ns", "dynamic_power_pw", "leakage_power_pw"):
        actual, reference = observed.get(metric), expected.get(metric)
        tolerance = float(tolerances.get(metric, 0.0))
        passed = actual is not None and reference is not None and abs(float(actual) - float(reference)) <= tolerance
        comparisons[metric] = {"expected": reference, "observed": actual, "absolute_tolerance": tolerance, "passed": passed}
    result = {
        "schema": "goalevolve.artifact-ae2.v1",
        "artifact_id": artifact["artifact_id"],
        "source": str(source),
        "source_hash": artifact["source_hash"],
        "openroad": str(openroad),
        "portable_tcl_sha256": _sha256(tcl),
        "rmp_standard_cells_lib": str(rmp_liberty) if rmp_liberty is not None else None,
        "flow_returncode": flow_rc,
        "parser_returncode": parser_rc,
        "official_check_returncode": official_rc,
        "official_check_detail": official_detail,
        "checkpoint_metrics": checkpoint_metrics,
        "comparisons": comparisons,
    }
    result["passed"] = flow_rc == 0 and parser_rc == 0 and official_rc == 0 and all(row["passed"] for row in comparisons.values())
    (report / "ae2_report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="GoalEvolve AE-2 artifact evaluation")
    parser.add_argument("mode", choices=("replay", "preflight"))
    parser.add_argument("--artifact", default="aes_cipher_top_student_code")
    parser.add_argument("--openroad", type=Path, help="prepared host executable for preflight only")
    parser.add_argument("--rebuild", action="store_true", help="configure and build the frozen AE-2 source before replay")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--verbose", action="store_true", help="stream CMake, build, parser, and flow logs to the terminal")
    args = parser.parse_args()
    artifact = _artifact(args.artifact)
    if args.mode == "preflight":
        result = ae2_preflight(artifact=artifact, openroad=args.openroad, verbose=args.verbose)
    else:
        result = ae2(
            artifact=artifact,
            openroad=args.openroad,
            jobs=args.jobs,
            rebuild=args.rebuild,
            verbose=args.verbose,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if bool(result["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
