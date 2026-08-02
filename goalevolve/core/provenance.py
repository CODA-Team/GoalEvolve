from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .io import sha256_file


def toolchain_fingerprint(*, source_root: Path | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"openroad_bin": None, "openroad_version": None, "openroad_sha256": None, "source_git_commit": None}
    if source_root and source_root.is_dir():
        for relative in (Path("build/bin/openroad"), Path("build_power/bin/openroad")):
            openroad_bin = source_root / relative
            if openroad_bin.is_file():
                payload["openroad_bin"] = str(openroad_bin.resolve())
                payload["openroad_sha256"] = sha256_file(openroad_bin)
                completed = subprocess.run([str(openroad_bin), "-version"], text=True, capture_output=True, check=False, timeout=30)
                payload["openroad_version"] = (completed.stdout or completed.stderr).strip()
                break
        top_level = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=source_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if top_level.returncode == 0 and Path(top_level.stdout.strip()).resolve() == source_root.resolve():
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            if completed.returncode == 0:
                payload["source_git_commit"] = completed.stdout.strip()
    return payload
