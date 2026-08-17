"""Private helpers shared by the artifact-evaluation entry points."""

from __future__ import annotations

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
    return {
        name: {**dict(payload), "artifact_id": name}
        for name, payload in dict(manifest.get("artifacts") or {}).items()
    }


def _path(relative: str) -> Path:
    return (PROJECT_ROOT / relative).resolve()


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log: Path,
    live: bool = False,
) -> int:
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
