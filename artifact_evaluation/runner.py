"""Artifact-evaluation entry point.

AE-1 checks that a release is complete and runnable on the current machine.
AE-2 rebuilds and replays a *fixed* released source artifact.  It deliberately
does not import Teacher, Student, retrieval, or any API credential.  Fresh,
non-deterministic evolution remains the separate AE-3 workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "artifact_evaluation" / "release_manifest.json"
SHIPPED_CONTEST_DESIGNS = (
    "aes_cipher_top",
    "ariane",
    "jpeg_encoder",
    "mempool_group",
    "nvdla_a",
    "nvdla_c",
    "nvdla_m",
    "nvdla_p",
)


def _json(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(name: str) -> dict[str, Any]:
    manifest = _json(MANIFEST_PATH)
    artifacts = dict(manifest.get("artifacts") or {})
    if name not in artifacts:
        raise SystemExit(f"unknown artifact {name!r}; available: {', '.join(sorted(artifacts))}")
    result = dict(artifacts[name])
    result["artifact_id"] = name
    return result


def _artifacts() -> dict[str, dict[str, Any]]:
    manifest = _json(MANIFEST_PATH)
    return {name: {**dict(payload), "artifact_id": name} for name, payload in dict(manifest.get("artifacts") or {}).items()}


def _path(relative: str) -> Path:
    return (PROJECT_ROOT / relative).resolve()


def _run(command: list[str], *, cwd: Path, env: dict[str, str], log: Path, live: bool = False) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(command) + "\n")
        stream.flush()
        if not live:
            process = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
            return process.returncode
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            stream.write(line)
            stream.flush()
            sys.stdout.write(line)
            sys.stdout.flush()
        return process.wait()


def _toolchain_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Preserve the OpenROAD/ORFS environment selected by the caller."""
    return dict(base or os.environ)


def _stage_source(*, source: Path, workspace: Path) -> Path:
    """Copy immutable lineage input before CMake writes generated version files."""
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(source, workspace, symlinks=True)
    return workspace


def _snapshot_matches(*, source: Path, manifest_path: Path) -> bool:
    if not manifest_path.is_file():
        return False
    from artifact_evaluation.verify_openroad_snapshot import snapshot_metadata

    manifest = _json(manifest_path)
    observed = snapshot_metadata(
        source,
        capture_excludes=manifest.get("capture_excludes") or (),
    )
    return all(
        observed[name] == manifest.get(name)
        for name in (
            "content_sha256",
            "regular_file_count",
            "symlink_count",
            "directory_count",
            "verified_no_external_symlinks",
        )
    )


def ae1(*, artifact: dict[str, Any]) -> dict[str, Any]:
    released = _artifacts()
    expected_root = _path(str(artifact["expected_root"]))
    source = _path(str(artifact["source_root"]))
    benchmark = _path(str(artifact["benchmark_root"]))
    required = {
        "release_manifest": MANIFEST_PATH,
        "frozen_source": source,
        "portable_tcl": expected_root / "evaluate.tcl",
        "expected_metrics": expected_root / "metrics.csv",
        "expected_evidence": expected_root / "evidence.json",
        "official_checker": PROJECT_ROOT / "third_party/official_checker/validity_check/def_validity_check.py",
        "official_parser": PROJECT_ROOT / "third_party/official_checker/evaluation/parse_log.py",
        "artifact_benchmark": benchmark / f"{artifact['design']}.def.gz",
        "asap7": benchmark.parents[1] / "asap7/setRC.tcl",
    }
    checks = {name: path.is_file() or path.is_dir() for name, path in required.items()}
    contest_root = benchmark.parent
    for design in SHIPPED_CONTEST_DESIGNS:
        design_root = contest_root / design
        checks[f"benchmark_{design}"] = (
            ((design_root / f"{design}.def.gz").is_file() or (design_root / f"{design}.def").is_file())
            and (design_root / f"{design}.v").is_file()
            and (design_root / f"{design}.sdc").is_file()
            and (design_root / "metrics.csv").is_file()
        )
    p0_manifest = PROJECT_ROOT / "artifact_evaluation/lineage/openroad_power/p0/source_manifest.json"
    checks["shared_openroad_p0_manifest"] = p0_manifest.is_file()
    if checks["shared_openroad_p0_manifest"]:
        from artifact_evaluation.verify_openroad_snapshot import snapshot_metadata

        manifest = _json(p0_manifest)
        observed = snapshot_metadata(
            p0_manifest.parent / "source",
            capture_excludes=manifest.get("capture_excludes") or (),
        )
        checks["shared_openroad_p0"] = all(
            observed[name] == manifest.get(name)
            for name in (
                "content_sha256",
                "regular_file_count",
                "symlink_count",
                "directory_count",
                "verified_no_external_symlinks",
            )
        )
    else:
        checks["shared_openroad_p0"] = False
    for artifact_id, released_artifact in released.items():
        released_source = _path(str(released_artifact["source_root"]))
        released_expected = _path(str(released_artifact["expected_root"]))
        released_manifest = released_expected / "source_manifest.json"
        checks[f"frozen_source_manifest_{artifact_id}"] = released_manifest.is_file()
        checks[f"frozen_source_{artifact_id}"] = _snapshot_matches(
            source=released_source,
            manifest_path=released_manifest,
        )
    tools = {name: shutil.which(name) is not None for name in ("cmake", "python3")}
    tools["openroad_on_path"] = shutil.which("openroad") is not None
    return {
        "schema": "goalevolve.artifact-ae1.v1",
        "artifact_id": artifact["artifact_id"],
        "passed": all(checks.values()) and tools["python3"],
        "paths": {name: str(path) for name, path in required.items()},
        "checks": checks,
        "tools": tools,
        "portable_tcl_sha256": _sha256(expected_root / "evaluate.tcl") if checks["portable_tcl"] else None,
        "released_artifacts": sorted(released),
    }


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
            "AE-2 requires the per-artifact OpenROAD build; use --openroad only with ae2-preflight, "
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
    print(f"[AE-2] Running post-route flow. Log: {output / 'evaluation.log'}", flush=True)
    flow_rc = _run([str(openroad), "-exit", str(tcl)], cwd=output, env=environment, log=output / "evaluation.log", live=verbose)
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
        "flow_returncode": flow_rc,
        "parser_returncode": parser_rc,
        "official_check_returncode": official_rc,
        "official_check_detail": official_detail,
        "comparisons": comparisons,
    }
    result["passed"] = flow_rc == 0 and parser_rc == 0 and official_rc == 0 and all(row["passed"] for row in comparisons.values())
    (report / "ae2_report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="GoalEvolve artifact evaluation")
    parser.add_argument("mode", choices=("ae1", "ae2", "ae2-preflight"))
    parser.add_argument("--artifact", default="aes_r58_student1")
    parser.add_argument("--openroad", type=Path, help="prepared host executable for ae2-preflight only")
    parser.add_argument("--rebuild", action="store_true", help="configure and build the frozen AE-2 source before replay")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--verbose", action="store_true", help="stream CMake, build, parser, and flow logs to the terminal")
    args = parser.parse_args()
    artifact = _artifact(args.artifact)
    if args.mode == "ae1":
        result = ae1(artifact=artifact)
    elif args.mode == "ae2-preflight":
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
