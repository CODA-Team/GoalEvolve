#!/usr/bin/env python3
"""Verify the project-owned shared OpenROAD p0 source snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable


def _update_text(digest: object, value: str) -> None:
    """Append an unambiguous UTF-8 record to the snapshot digest."""
    digest.update(value.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")


def snapshot_metadata(
    source: Path,
    *,
    capture_excludes: Iterable[str] = (),
) -> dict[str, object]:
    """Hash names, regular-file contents, and symlink targets deterministically."""
    source = source.resolve()
    excluded_names = {str(name) for name in capture_excludes if str(name)}
    digest = hashlib.sha256()
    regular_files = 0
    symlinks = 0
    directories = 0
    external_symlinks: list[str] = []

    for root, directory_names, file_names in os.walk(source, followlinks=False):
        # os.walk preserves the filesystem's enumeration order unless this is
        # sorted in-place. The manifest must be stable across machines.
        directory_names[:] = sorted(
            name for name in directory_names if name not in excluded_names
        )
        root_path = Path(root)
        for name in sorted(directory_names):
            path = root_path / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                symlinks += 1
                _update_text(digest, "L")
                _update_text(digest, relative)
                target = os.readlink(path)
                _update_text(digest, target)
                if not _target_is_internal(source, path, target):
                    external_symlinks.append(relative)
            else:
                directories += 1
                _update_text(digest, "D")
                _update_text(digest, relative)
        for name in sorted(file_names):
            if name in excluded_names:
                continue
            path = root_path / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                symlinks += 1
                _update_text(digest, "L")
                _update_text(digest, relative)
                target = os.readlink(path)
                _update_text(digest, target)
                if not _target_is_internal(source, path, target):
                    external_symlinks.append(relative)
                continue
            if not path.is_file():
                continue
            regular_files += 1
            _update_text(digest, "F")
            _update_text(digest, relative)
            _update_text(digest, str(path.stat().st_size))
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)

    return {
        "content_sha256": digest.hexdigest(),
        "regular_file_count": regular_files,
        "symlink_count": symlinks,
        "directory_count": directories,
        "verified_no_external_symlinks": not external_symlinks,
        "external_symlinks": external_symlinks,
    }


def _target_is_internal(source: Path, link: Path, target: str) -> bool:
    resolved = Path(os.path.abspath(os.path.join(link.parent, target)))
    try:
        resolved.relative_to(source)
    except ValueError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parent / "lineage" / "openroad_power" / "p0" / "source_manifest.json",
    )
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = manifest_path.parent / "source"
    observed = snapshot_metadata(
        source,
        capture_excludes=manifest.get("capture_excludes") or (),
    )
    if not args.verify:
        print(json.dumps(observed, indent=2, sort_keys=True))
        return 0
    expected = {
        name: manifest.get(name)
        for name in ("content_sha256", "regular_file_count", "symlink_count", "directory_count", "verified_no_external_symlinks")
    }
    actual = {name: observed[name] for name in expected}
    print(json.dumps({"expected": expected, "actual": actual}, indent=2, sort_keys=True))
    return 0 if expected == actual else 1


if __name__ == "__main__":
    raise SystemExit(main())
