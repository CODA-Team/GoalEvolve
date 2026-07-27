from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Sequence

from ..core.models import CandidateResult


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    changed_files: tuple[str, ...]
    violations: tuple[str, ...]
    require_cpp_patch: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def preflight_candidate(
    candidate: CandidateResult,
    *,
    allowed_patch_roots: Sequence[str] = (),
    allowed_patch_paths: Sequence[str] = (),
    require_cpp_patch: bool = True,
) -> PreflightReport:
    changed = tuple(sorted(set(re.findall(r"^\+\+\+ b/(.+)$", candidate.implementation_diff, flags=re.MULTILINE))))
    violations: list[str] = []
    if not changed:
        violations.append("missing_changed_files_in_unified_diff")
    if require_cpp_patch and not any(path.endswith((".cc", ".cpp", ".cxx", ".hh", ".hpp")) for path in changed):
        violations.append("missing_cpp_patch")
    normalized = tuple(root.rstrip("/") for root in allowed_patch_roots)
    for path in changed:
        if normalized and not any(path == root or path.startswith(root + "/") for root in normalized):
            violations.append(f"outside_allowed_patch_surface:{path}")
    exact_paths = frozenset(path.rstrip("/") for path in allowed_patch_paths)
    for path in changed:
        if exact_paths and path not in exact_paths:
            violations.append(f"outside_assigned_patch_scope:{path}")
    if not candidate.source_commit:
        violations.append("missing_source_commit")
    return PreflightReport(not violations, changed, tuple(violations), require_cpp_patch)
