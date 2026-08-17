"""AE-1 release completeness and path validation entry point."""

from __future__ import annotations

import argparse
import json
import shutil
from typing import Any

from artifact_evaluation._common import (
    MANIFEST_PATH,
    PROJECT_ROOT,
    SHIPPED_CONTEST_DESIGNS,
    _artifact,
    _artifacts,
    _json,
    _path,
    _sha256,
    _snapshot_matches,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="GoalEvolve AE-1 artifact validation")
    parser.add_argument("--artifact", default="aes_cipher_top_student_code")
    args = parser.parse_args()
    result = ae1(artifact=_artifact(args.artifact))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if bool(result["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
