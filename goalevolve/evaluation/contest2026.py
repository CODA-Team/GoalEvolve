from __future__ import annotations

import csv
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..execution.execution import ExecutionPolicy, ResilientCommandRunner
from ..execution.workspace import clone_source_tree
from ..core.models import CandidateResult, CheckResult, Hypothesis, Parent
from ..execution.preflight import preflight_candidate
from .sfinal import observe_sfinal
from ..planning.timing_recovery import timing_recipe


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_ROOT = PROJECT_ROOT / "third_party" / "official_checker"
OFFICIAL_CHECKER = OFFICIAL_ROOT / "validity_check" / "def_validity_check.py"
OFFICIAL_EQUIV_CELLS = OFFICIAL_ROOT / "validity_check" / "asap7_equivalent_cell_list.csv"
OFFICIAL_UTILS_TCL = OFFICIAL_ROOT / "validity_check" / "OpenROAD_utils.tcl"
OFFICIAL_PARSE_LOG = OFFICIAL_ROOT / "evaluation" / "parse_log.py"
_PHASE_SIGNAL = re.compile(r"METRIC\|([A-Za-z0-9_]+)\|([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")
_CHECKPOINT_BLOCK = re.compile(r"GOALEVOLVE_CHECKPOINT_BEGIN\s+(\S+)(.*?)GOALEVOLVE_CHECKPOINT_END\s+\1", re.DOTALL)
_CHECKPOINT_VALUE = re.compile(r"GOALEVOLVE_CHECKPOINT_METRIC\s+(\S+)\s+(tns_abs_ns|wns_abs_ns)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")
_TNS = re.compile(r"^\s*tns\s+max\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*$", re.MULTILINE | re.IGNORECASE)
_WNS = re.compile(r"^\s*wns\s+max\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*$", re.MULTILINE | re.IGNORECASE)
_TOTAL_POWER = re.compile(r"^\s*Total\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)", re.MULTILINE)
_VERILOG_INSTANCE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+([^\s(]+)\s*\(", re.MULTILINE)
# `check_placement -verbose` emits normal INFO records before reporting its
# result.  DPL-0005 (diamond-search bounds) and DPL-0006 (utilization) occur
# in successful detailed placement logs, so matching their bare IDs makes a
# legal candidate fail.  Reject only ERROR-level DPL output (normally
# DPL-0033) and explicit checker failure messages.
_PLACEMENT_FAILURE = re.compile(
    r"\[ERROR\s+DPL-\d+\]|check_placement reported errors|Placement NOT legal",
    re.IGNORECASE,
)
# CMake writes this source-tree header from the current git describe/hash.
# It is build metadata, never an algorithmic source edit or an allowed patch.
_GENERATED_SOURCE_METADATA = frozenset({Path("include/ord/Version.hh")})

# ``estimate_parasitics`` invalidates timing, but the pinned OpenSTA builds do
# not consistently invalidate their per-instance power cache.  Changing and
# immediately restoring the global activity model invalidates both activity
# propagation and instance power without changing the model used by reports.
_POWER_CACHE_REFRESH_TCL = (
    "set_power_activity -global -activity 0.1 -duty 0.5",
    "unset_power_activity -global",
)


def _fresh_stage_qor_tcl(*, parasitics_command: str) -> list[str]:
    """Return the fixed pre-report boundary for authoritative stage QoR."""
    return [parasitics_command, *_POWER_CACHE_REFRESH_TCL]


def _project_toolchain() -> tuple[dict[str, str] | None, tuple[str, ...]]:
    """Use the matching OpenROAD/ORFS environment activated by the caller."""
    return dict(os.environ), ()


@dataclass(frozen=True)
class Contest2026Config:
    design: str
    benchmark_root: Path
    source_seed: Path
    # Optional immutable checkout carrying a build graph compatible with the
    # source lineage.  It is a build-cache donor only: source comparison,
    # patch provenance, and candidate evaluation remain rooted at
    # ``source_seed``.
    build_seed_root: Path | None = None
    build_dir_name: str = "build_goalevolve"
    build_jobs: int = 2
    allowed_patch_roots: tuple[str, ...] = ()
    require_cpp_patch: bool = True
    power_reclaim_phase: str = "early_forced_reclaim"
    power_reclaim_proportion_percent: float = 80.0
    power_reclaim_max_moves: int = 0
    power_stage_tns_ceiling_ns: float = 30.0
    execution_policy: ExecutionPolicy = ExecutionPolicy()
    toolchain_environment: dict[str, str] | None = None
    toolchain_cmake_args: tuple[str, ...] = ()

    @property
    def benchmark_dir(self) -> Path:
        return self.benchmark_root / self.design


def _single(path: Path, patterns: Iterable[str]) -> Path:
    for pattern in patterns:
        matches = sorted(path.glob(pattern))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"no file matching {list(patterns)} under {path}")


def _escape_tcl(path: Path) -> str:
    return "{" + str(path.resolve()).replace("}", "\\}") + "}"


def _liberty_matching_brace(text: str, opening: int) -> int:
    """Return the matching closing brace, ignoring Liberty strings/comments."""
    depth = 0
    quote: str | None = None
    escaped = False
    block_comment = False
    index = opening
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
                continue
        elif quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        elif char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError("unbalanced braces in Liberty input")


_LIBRARY_OPEN = re.compile(r"\blibrary\s*\([^)]*\)\s*\{")
_CELL_OPEN = re.compile(r"\bcell\s*\([^)]*\)\s*\{")


def _liberty_cell_blocks(text: str) -> list[str]:
    """Extract complete top-level cell blocks from one Liberty library."""
    library = _LIBRARY_OPEN.search(text)
    if library is None:
        raise ValueError("Liberty input has no library (...) block")
    opening = text.find("{", library.start(), library.end())
    closing = _liberty_matching_brace(text, opening)
    cells: list[str] = []
    depth = 1
    quote: str | None = None
    escaped = False
    block_comment = False
    cursor = opening + 1
    while cursor < closing:
        char = text[cursor]
        following = text[cursor + 1] if cursor + 1 < closing else ""
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                cursor += 2
                continue
        elif quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "/" and following == "*":
            block_comment = True
            cursor += 2
            continue
        elif char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 1 and text.startswith("cell", cursor):
            before = text[cursor - 1] if cursor > opening + 1 else " "
            after = cursor + len("cell")
            if not (before.isalnum() or before == "_"):
                while after < closing and text[after].isspace():
                    after += 1
                if after < closing and text[after] == "(":
                    match = _CELL_OPEN.match(text, cursor)
                    if match is not None:
                        cell_opening = text.find("{", match.start(), match.end())
                        cell_closing = _liberty_matching_brace(text, cell_opening)
                        if cell_closing > closing:
                            raise ValueError("cell block escapes the enclosing Liberty library")
                        cells.append(text[match.start():cell_closing + 1])
                        cursor = cell_closing + 1
                        continue
        cursor += 1
    return cells


def _write_rmp_combined_liberty(lib_files: Iterable[Path], output: Path) -> Path:
    """Materialize RMP's one-file standard-cell ABC library.

    ABC's ``read_lib`` replaces rather than augments its current gate library.
    OpenROAD needs all 15 ASAP7 standard-cell Liberty files for normal STA,
    while RMP/ABC needs their cells in one input.  This evaluation-local file
    preserves the first library's units/templates and appends every other
    standard-cell ``cell`` block.  SRAM macros intentionally stay out: they
    are not combinational gates for ABC mapping.
    """
    files = sorted(Path(path) for path in lib_files)
    standard = [path for path in files if path.name.startswith("asap7sc7p5t_")]
    # Unit tests and non-ASAP7 integrations can still use the helper, while
    # production ASAP7 selection remains exact and excludes sram_*.lib.
    selected = standard or [path for path in files if not path.name.lower().startswith("sram")]
    if not selected:
        raise ValueError("no standard-cell Liberty files available for RMP")
    texts = [(path, path.read_text(encoding="utf-8")) for path in selected]
    first_path, first_text = texts[0]
    library = _LIBRARY_OPEN.search(first_text)
    if library is None:
        raise ValueError(f"RMP Liberty seed has no library block: {first_path}")
    opening = first_text.find("{", library.start(), library.end())
    closing = _liberty_matching_brace(first_text, opening)
    groups: list[str] = []
    for _, text in texts[1:]:
        groups.extend(_liberty_cell_blocks(text))
    merged = first_text[:closing]
    if groups:
        merged += "\n\n/* GoalEvolve RMP standard-cell merge. */\n" + "\n\n".join(groups) + "\n"
    merged += first_text[closing:]
    destination = output / "rmp_standard_cells.lib"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(merged, encoding="utf-8")
    manifest = {
        "purpose": "single ABC gate library for RMP delay restructure",
        "seed": str(first_path.resolve()),
        "sources": [str(path.resolve()) for path, _ in texts],
        "cell_group_count": len(_liberty_cell_blocks(first_text)) + len(groups),
        "sha256": hashlib.sha256(merged.encode("utf-8")).hexdigest(),
    }
    (output / "rmp_standard_cells.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def _goal_tns_abs_ns(contract) -> float | None:
    for metric in contract.metrics:
        if metric.name == "tns_abs_ns" and metric.minimize:
            return float(metric.target)
    return None


def _source_diff(
    seed: Path,
    candidate: Path,
    *,
    limit: int = 200_000,
    allowed_roots: tuple[str, ...] = (),
) -> str:
    chunks: list[str] = []
    suffixes = {".cc", ".cpp", ".cxx", ".hh", ".hpp", ".h", ".tcl"}
    for path in _evolution_source_files(candidate, suffixes, allowed_roots=allowed_roots):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        relative = path.relative_to(candidate)
        if relative in _GENERATED_SOURCE_METADATA:
            continue
        original = seed / relative
        before = original.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True) if original.is_file() else []
        after = path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
        if before == after:
            continue
        chunks.extend(difflib.unified_diff(before, after, fromfile=f"a/{relative}", tofile=f"b/{relative}"))
        if sum(len(item) for item in chunks) >= limit:
            chunks.append("# diff truncated by GoalEvolve v2\n")
            break
    return "".join(chunks)


def _tree_hash(root: Path, *, allowed_roots: tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256()
    for path in _evolution_source_files(
        root,
        {".cc", ".cpp", ".cxx", ".hh", ".hpp", ".h", ".tcl"},
        allowed_roots=allowed_roots,
    ):
        relative = path.relative_to(root)
        if relative in _GENERATED_SOURCE_METADATA:
            continue
        digest.update(str(relative).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _workspace_parent_source(workspace: Path) -> Path | None:
    """Return the immutable common-parent snapshot recorded for a workspace.

    Older workspaces do not carry this field, so callers intentionally retain
    their configured seed fallback for replay compatibility.
    """
    manifest = workspace.parent / "workspace_manifest.json"
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    candidate = Path(str(raw.get("parent_source") or ""))
    return candidate if candidate.is_dir() else None


def _evolution_source_files(
    root: Path,
    suffixes: set[str],
    *,
    allowed_roots: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    """Enumerate exactly the source surface that a Student may edit.

    A full ``src/`` walk still includes OpenROAD's vendored and test-heavy
    subtrees (about 1.5 GB in the AES seed), even when the Student contract
    permits only a few mechanism directories.  Provenance must use the same
    allowlist as preflight: otherwise an edit turn can finish but the
    controller spends hours hashing source it would reject.  Unit tests and
    callers without a configured scope retain the ``src/`` fallback.
    """
    configured = tuple(Path(item) for item in allowed_roots if item)
    bases = tuple(root / relative for relative in configured if (root / relative).is_dir())
    if not bases:
        surface = root / "src"
        bases = (surface if surface.is_dir() else root,)
    return tuple(
        sorted(
            path
            for base in bases
            for path in base.rglob("*")
            if path.is_file() and path.suffix in suffixes
        )
    )


def _read_metrics(path: Path) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    # The official flow may write an early, partially populated row before
    # the final design record.  In particular, that row can contain zero DRV
    # fields while leaving design, timing, and power blank.  Do not mistake it
    # for the final QoR result: prefer the last named design record, then keep
    # the old last-nonempty-row behavior for minimal/unit CSVs without design.
    named_rows = [row for row in rows if (row.get("design") or "").strip()]
    nonempty_rows = [
        row for row in rows if any((value or "").strip() for value in row.values())
    ]
    row = (named_rows or nonempty_rows or [None])[-1]
    if not row:
        raise ValueError(f"official metrics parser produced no row: {path}")
    aliases = {
        "tns_abs_ns": "tns",
        "leakage_power_pw": "leakage_power",
        "drv_count": "slew_over_count",
    }
    metrics: dict[str, float] = {}
    for target, source in aliases.items():
        raw = row.get(source)
        if raw not in (None, ""):
            metrics[target] = abs(float(raw)) if target == "tns_abs_ns" else float(raw)
    # QoR-gap power terms deliberately exclude wall-clock runtime.  Dynamic
    # power is the official parser's total power minus leakage power, in the
    # same pW unit as ``leakage_power_pw``.
    total_power = row.get("total_power")
    leakage_power = row.get("leakage_power")
    if total_power not in (None, "") and leakage_power not in (None, ""):
        metrics["dynamic_power_pw"] = float(total_power) - float(leakage_power)
    # The official parser reports a tool-only runtime when OpenROAD exposes
    # one; a valid completed flow may instead provide only flow_runtime.
    # Treat the latter as the same campaign metric rather than asking a
    # Student to edit C++ for a controller-side CSV omission.
    runtime = row.get("tool_runtime") or row.get("flow_runtime")
    if runtime not in (None, ""):
        metrics["runtime_s"] = float(runtime)
    return metrics


def _observed_phase_signals(log: Path, expected: Iterable[str]) -> dict[str, float]:
    """Read source-emitted telemetry; successful flow is not mechanism attribution."""
    if not log.is_file():
        return {}
    observed: dict[str, float] = {}
    for name, value in _PHASE_SIGNAL.findall(log.read_text(encoding="utf-8", errors="ignore")):
        observed[name] = observed.get(name, 0.0) + float(value)
    # A rollback counter of zero is a real, useful observation: it means the
    # mechanism recorded its journal outcome and did not need to undo a move.
    # Preserve expected zero-valued signals so the evidence layer can tell it
    # apart from a missing telemetry line.
    return {name: observed[name] for name in expected if name in observed}


def _placement_legal(log: Path) -> tuple[bool, str]:
    """Verify the one post-legalization placement check in the official log."""
    if not log.is_file():
        return False, "placement_log_missing"
    text = log.read_text(encoding="utf-8", errors="ignore")
    failures = sorted(set(_PLACEMENT_FAILURE.findall(text)))
    if failures:
        return False, "placement_violation:" + ",".join(failures)
    if "Placement legalized." not in text:
        return False, "placement_success_marker_missing"
    return True, "post_detailed_placement"


def _checkpoint_metrics(log: Path) -> dict[str, object]:
    checkpoints: dict[str, dict[str, float]] = {}
    if not log.is_file():
        return {"schema_version": "goalevolve.v2.checkpoints.v1", "checkpoints": checkpoints}
    text = log.read_text(encoding="utf-8", errors="ignore")
    for stage, block in _CHECKPOINT_BLOCK.findall(text):
        metrics: dict[str, float] = {}
        # Checkpoints use hidden STA queries serialized as custom lines.
        # Printing ``report_tns`` before the final report is invalid because
        # the reference parser intentionally accepts the first matching
        # TNS/WNS in the log as the scored result.
        for metric_stage, name, value in _CHECKPOINT_VALUE.findall(block):
            if metric_stage == stage:
                metrics[name] = abs(float(value))
        tns = _TNS.search(block)
        wns = _WNS.search(block)
        power = _TOTAL_POWER.search(block)
        if tns and "tns_abs_ns" not in metrics:
            metrics["tns_abs_ns"] = abs(float(tns.group(1)))
        if wns and "wns_abs_ns" not in metrics:
            metrics["wns_abs_ns"] = abs(float(wns.group(1)))
        if power:
            leakage_power_pw = float(power.group(3)) * 1e12
            total_power_pw = float(power.group(4)) * 1e12
            metrics["leakage_power_pw"] = leakage_power_pw
            metrics["total_power_pw"] = total_power_pw
            metrics["dynamic_power_pw"] = total_power_pw - leakage_power_pw
        checkpoints[stage] = metrics
    return {"schema_version": "goalevolve.v2.checkpoints.v1", "checkpoints": checkpoints}


def _verilog_cell_map(path: Path) -> dict[str, str]:
    """Read library-cell-to-instance snapshots emitted at controller checkpoints.

    This intentionally records only structural replacement evidence.  It does
    not pretend that a type change alone proves a timing or power benefit.
    """
    if not path.is_file():
        return {}
    ignored = {"module", "primitive", "if", "for", "generate", "assign"}
    result: dict[str, str] = {}
    for cell, instance in _VERILOG_INSTANCE.findall(path.read_text(encoding="utf-8", errors="ignore")):
        if cell.lower() not in ignored:
            result[instance] = cell
    return result


def _power_timing_cell_tradeoff(output: Path) -> dict[str, object]:
    """Quantify cells reclaimed by power then changed again by timing repair."""
    before = _verilog_cell_map(output / "post_repair_design.v")
    after_power = _verilog_cell_map(output / "post_repair_power.v")
    after_timing = _verilog_cell_map(output / "post_repair_timing.v")
    if not before or not after_power:
        return {
            "schema_version": "goalevolve.v2.power-timing-cell-tradeoff.v1",
            "available": False,
            "reason": "pre_power_or_post_power_checkpoint_missing_or_unparseable",
        }
    power_changes = {
        instance: (before[instance], cell)
        for instance, cell in after_power.items()
        if instance in before and before[instance] != cell
    }
    power_replacements = [
        {
            "instance": instance,
            "before_power": cells[0],
            "after_power": cells[1],
        }
        for instance, cells in sorted(power_changes.items())
    ]

    def transition_summary(changes: dict[str, tuple[str, str]]) -> list[dict[str, object]]:
        counts: dict[tuple[str, str], int] = {}
        for before_cell, after_cell in changes.values():
            key = (before_cell, after_cell)
            counts[key] = counts.get(key, 0) + 1
        return [
            {"before": key[0], "after": key[1], "count": count}
            for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    result: dict[str, object] = {
        "schema_version": "goalevolve.v2.power-timing-cell-tradeoff.v2",
        "available": True,
        "power_reclaim_available": True,
        "timing_interaction_available": bool(after_timing),
        "power_reclaim_replacements": len(power_changes),
        # This full list is an audit artifact, not prompt text.  Teacher gets
        # only compact counts/top transitions through schedule memory.
        "power_replacements": power_replacements,
        "power_transition_histogram": transition_summary(power_changes),
        "power_transition_top": transition_summary(power_changes)[:16],
        "power_replacement_examples": power_replacements[:32],
    }
    if not after_timing:
        result["timing_interaction_reason"] = "post_repair_timing_checkpoint_not_applicable"
        return result
    timing_changes = {
        instance: (after_power[instance], cell)
        for instance, cell in after_timing.items()
        if instance in after_power and after_power[instance] != cell
    }
    overlap = sorted(set(power_changes) & set(timing_changes))
    exact_reversions = [
        instance for instance in overlap
        if timing_changes[instance][1] == power_changes[instance][0]
    ]
    timing_replacements = [
        {
            "instance": instance,
            "after_power": cells[0],
            "after_timing": cells[1],
        }
        for instance, cells in sorted(timing_changes.items())
    ]
    result.update({
        "power_reclaim_replacements": len(power_changes),
        "timing_repair_replacements": len(timing_changes),
        "power_to_timing_overlap": len(overlap),
        "exact_power_to_timing_reversions": len(exact_reversions),
        "overlap_rate_of_power_replacements": (len(overlap) / len(power_changes)) if power_changes else 0.0,
        "exact_reversion_rate_of_power_replacements": (len(exact_reversions) / len(power_changes)) if power_changes else 0.0,
        "timing_replacements": timing_replacements,
        "timing_transition_histogram": transition_summary(timing_changes),
        "timing_transition_top": transition_summary(timing_changes)[:16],
        "examples": [
            {
                "instance": instance,
                "before_power": power_changes[instance][0],
                "after_power": power_changes[instance][1],
                "after_timing": timing_changes[instance][1],
                "exact_reversion": instance in exact_reversions,
            }
            for instance in overlap[:32]
        ],
    })
    return result


def official_four_check(
    *,
    pre_opt: Path,
    post_opt: Path,
    output_log: Path,
    policy: ExecutionPolicy,
    environment: dict[str, str] | None = None,
) -> tuple[bool, str]:
    runner = ResilientCommandRunner(policy)
    report = runner.run(
        command=[
            "python3",
            str(OFFICIAL_CHECKER),
            "--pre_opt",
            str(pre_opt),
            "--post_opt",
            str(post_opt),
            "--equiv_cells",
            str(OFFICIAL_EQUIV_CELLS),
        ],
        cwd=post_opt,
        environment=environment,
    )
    output_log.write_text(report.stdout_tail + "\n" + report.stderr_tail, encoding="utf-8")
    passed = report.ok and "SUMMARY: 4/4 checks passed" in report.stdout_tail
    return passed, "official_4of4_pass" if passed else (report.resource_error or "official_4of4_failed")


class Contest2026OpenROADEvaluator:
    """Built-in private-source OpenROAD evaluator using the official reference checks."""

    name = "contest_openroad"

    def __init__(self, config: Contest2026Config) -> None:
        self.config = config
        # Edits can be parallel, but QoR measurement must be exclusive: a
        # concurrent OpenROAD flow turns runtime into scheduler noise.
        self._measurement_lock = threading.Lock()

    def effective_power_reclaim_profile(self) -> dict[str, object]:
        """Return the exact repair_power arguments emitted into every Tcl flow."""
        return {
            "phase": self.config.power_reclaim_phase,
            "proportion_percent": self.config.power_reclaim_proportion_percent,
            "max_moves": self.config.power_reclaim_max_moves,
        }

    def baseline_identity(
        self,
        *,
        optimization_mode: str,
        timing_recipe_id: str | None = None,
        goal_tns_abs_ns: float | None = None,
    ) -> dict[str, object]:
        """Return the immutable identity of a parent-flow intervention.

        Parent baselines are only comparable to a candidate when the generated
        Tcl sequence is identical.  Hashing this entire evaluator module used
        to invalidate every expensive baseline when unrelated recovery,
        parsing, or reporting code changed.  Fingerprint the normalized Tcl
        program itself, including the selected recipe and frozen target.
        """
        recipe = (
            timing_recipe(timing_recipe_id).to_dict()
            if timing_recipe_id is not None
            else None
        )
        schedule_hash = self._baseline_tcl_hash(
            optimization_mode=optimization_mode,
            timing_recipe_id=timing_recipe_id or "legacy_setup",
            goal_tns_abs_ns=goal_tns_abs_ns,
        )
        return {
            "schema": "contest2026-parent-flow.v3",
            "normalized_tcl_sha256": schedule_hash,
            "optimization_mode": optimization_mode,
            "timing_recipe": recipe,
            "power_reclaim": {
                **self.effective_power_reclaim_profile(),
                "stage_tns_ceiling_ns": self.config.power_stage_tns_ceiling_ns,
            },
        }

    def baseline_artifact_compatible(
        self,
        *,
        baseline_root: Path,
        optimization_mode: str,
        timing_recipe_id: str,
        goal_tns_abs_ns: float | None = None,
    ) -> bool:
        """Prove that a pre-v3 cached baseline used the current Tcl schedule."""
        existing = baseline_root / "evaluate.tcl"
        if not existing.is_file():
            return False
        expected_hash = self._baseline_tcl_hash(
            optimization_mode=optimization_mode,
            timing_recipe_id=timing_recipe_id,
            goal_tns_abs_ns=goal_tns_abs_ns,
        )
        text = existing.read_text(encoding="utf-8", errors="ignore")
        normalized = text.replace(str(baseline_root.resolve()), "<GOALEVOLVE_OUTPUT>")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest() == expected_hash

    def _baseline_tcl_hash(
        self,
        *,
        optimization_mode: str,
        timing_recipe_id: str,
        goal_tns_abs_ns: float | None,
    ) -> str:
        """Materialize and hash a path-normalized controller Tcl program."""
        with tempfile.TemporaryDirectory(prefix="goalevolve_tcl_identity_") as raw:
            root = Path(raw)
            tcl = root / "evaluate.tcl"
            self._write_tcl(
                tcl=tcl,
                output=root,
                goal_tns_abs_ns=goal_tns_abs_ns,
                optimization_mode=optimization_mode,
                timing_recipe_id=timing_recipe_id,
            )
            text = tcl.read_text(encoding="utf-8", errors="strict")
            normalized = text.replace(str(root.resolve()), "<GOALEVOLVE_OUTPUT>")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def evaluate_baseline(self, *, contract, output: Path) -> dict[str, object]:
        """Measure the immutable seed with the exact candidate evaluation flow.

        This is deliberately separate from candidate evaluation: baseline has
        no source diff and must not pretend to be a Student implementation.
        The resulting artifact is the authoritative p0 input for EPD and for
        later same-seed A/B reports. A released source snapshot intentionally
        omits CMake products, so baseline owns a private build workspace just
        like a Student evaluation. ``build_seed_root`` may accelerate that
        build but is never required for a portable design profile.
        """
        output.mkdir(parents=True, exist_ok=True)
        workspace = output / "workspace"
        if workspace.exists():
            shutil.rmtree(workspace)
        # CMake generates include/ord/Version.hh in its source directory.
        # p0 is a shared release snapshot, so give the baseline the same
        # private source ownership that a Student evaluation receives.
        source = workspace / "source"
        clone_source_tree(self.config.source_seed, source)
        configure_log, build_log = output / "configure.log", output / "build.log"
        try:
            binary = self._build(
                source,
                workspace,
                configure_log=configure_log,
                build_log=build_log,
            )
            tcl = output / "evaluate.tcl"
            self._write_tcl(tcl=tcl, output=output)
        except Exception as exc:
            return {
                "schema_version": "goalevolve.v2.baseline.v1",
                "ok": False,
                "metrics": {},
                "checks": [{"name": "build", "passed": False, "detail": f"{type(exc).__name__}:{exc}"}],
                "artifacts": {"configure_log": str(configure_log), "build_log": str(build_log)},
            }
        log, metrics_csv = output / "evaluation.log", output / "metrics.csv"
        if metrics_csv.exists():
            metrics_csv.unlink()
        runner = ResilientCommandRunner(self.config.execution_policy)
        flow = runner.run(command=[str(binary), "-exit", str(tcl)], cwd=output, output_log=log, environment=self.config.toolchain_environment)
        checkpoint_path = output / "checkpoint_metrics.json"
        checkpoint_path.write_text(json.dumps(_checkpoint_metrics(log), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        parsed = runner.run(command=["python3", str(OFFICIAL_PARSE_LOG), str(log), "--csv", str(metrics_csv)], cwd=output, environment=self.config.toolchain_environment)
        metrics = _read_metrics(metrics_csv) if parsed.ok and metrics_csv.is_file() else {}
        required = {spec.name for spec in contract.metrics}
        metrics_ok = parsed.ok and required.issubset(metrics)
        placement_ok = False
        placement_detail = "flow_or_metrics_failed"
        lec_ok = False
        lec_detail = "flow_or_metrics_failed"
        official_log = output / "official_4of4.log"
        if flow.ok and metrics_ok:
            placement_ok, placement_detail = _placement_legal(log)
            if placement_ok:
                lec_ok, lec_detail = official_four_check(pre_opt=self.config.benchmark_dir, post_opt=output, output_log=official_log, policy=self.config.execution_policy, environment=self.config.toolchain_environment)
            else:
                lec_detail = "placement_failed"
        checks = [
            {"name": "build", "passed": True, "detail": str(binary)},
            {"name": "flow", "passed": flow.ok, "detail": flow.resource_error or "official_openroad_flow"},
            {"name": "metrics", "passed": metrics_ok, "detail": "official_parse_log" if metrics_ok else f"missing_metrics:{','.join(sorted(required - set(metrics)))}"},
            {"name": "placement", "passed": placement_ok, "detail": placement_detail},
            {"name": "lec", "passed": lec_ok, "detail": lec_detail},
        ]
        sfinal_artifact = _observe_sfinal(design=self.config.design, benchmark_dir=self.config.benchmark_dir, output=output)
        return {
            "schema_version": "goalevolve.v2.baseline.v1",
            "ok": all(bool(check["passed"]) for check in checks),
            "metrics": metrics,
            "checks": checks,
            "artifacts": {
                "openroad_binary": str(binary),
                "configure_log": str(configure_log),
                "build_log": str(build_log),
                "evaluation_log": str(log),
                "metrics_csv": str(metrics_csv),
                "checkpoint_metrics": str(checkpoint_path),
                "official_4of4_log": str(official_log),
                "checkpoint_post_route_db": str(output / "post_route.odb"),
                **sfinal_artifact,
            },
        }

    def evaluate_parent(
        self,
        *,
        contract,
        parent: Parent,
        source: Path,
        output: Path,
        optimization_mode: str,
        timing_recipe_id: str = "legacy_setup",
    ) -> dict[str, object]:
        """Remeasure one immutable parent using a newly selected flow mode.

        Power-first evolution intentionally changes the Tcl sequence after a
        power-complete promotion.  Comparing a ``power_only`` parent directly
        with a ``power_then_timing`` child would incorrectly credit the
        built-in timing pass to the child's source edit.  This method creates
        the same private build and official post-route/4-of-4 evidence used
        for a Student, but with no source delta, so it is an explicit stage
        baseline rather than a candidate.
        """
        output.mkdir(parents=True, exist_ok=True)
        workspace = output / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        configure_log = output / "configure.log"
        build_log = output / "build.log"
        try:
            binary = self._build(source, workspace, configure_log=configure_log, build_log=build_log)
            tcl = output / "evaluate.tcl"
            log = output / "evaluation.log"
            metrics_csv = output / "metrics.csv"
            self._write_tcl(
                tcl=tcl,
                output=output,
                goal_tns_abs_ns=_goal_tns_abs_ns(contract),
                optimization_mode=optimization_mode,
                timing_recipe_id=timing_recipe_id,
            )
        except Exception as exc:
            return {
                "schema_version": "goalevolve.v2.stage-baseline.v1",
                "ok": False,
                "metrics": {},
                "checks": [{"name": "build", "passed": False, "detail": f"{type(exc).__name__}:{exc}"}],
                "artifacts": {"configure_log": str(configure_log), "build_log": str(build_log)},
            }

        with self._measurement_lock:
            runner = ResilientCommandRunner(self.config.execution_policy)
            flow = runner.run(command=[str(binary), "-exit", str(tcl)], cwd=output, output_log=log, environment=self.config.toolchain_environment)
            checkpoint_path = output / "checkpoint_metrics.json"
            checkpoint_path.write_text(
                json.dumps(_checkpoint_metrics(log), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            parsed = runner.run(command=["python3", str(OFFICIAL_PARSE_LOG), str(log), "--csv", str(metrics_csv)], cwd=output, environment=self.config.toolchain_environment)
            metrics = _read_metrics(metrics_csv) if parsed.ok and metrics_csv.is_file() else {}
            required = {spec.name for spec in contract.metrics}
            metrics_ok = parsed.ok and required.issubset(metrics)
            official_log = output / "official_4of4.log"
            placement_ok = False
            placement_detail = "flow_or_metrics_failed"
            lec_ok = False
            lec_detail = "flow_or_metrics_failed"
            if flow.ok and metrics_ok:
                placement_ok, placement_detail = _placement_legal(log)
                if placement_ok:
                    lec_ok, lec_detail = official_four_check(
                        pre_opt=self.config.benchmark_dir,
                        post_opt=output,
                        output_log=official_log,
                        policy=self.config.execution_policy,
                        environment=self.config.toolchain_environment,
                    )
                else:
                    lec_detail = "placement_failed"
            checks = [
                {"name": "build", "passed": True, "detail": str(binary)},
                {"name": "flow", "passed": flow.ok, "detail": flow.resource_error or "official_openroad_flow"},
                {"name": "metrics", "passed": metrics_ok, "detail": "official_parse_log" if metrics_ok else f"missing_metrics:{','.join(sorted(required - set(metrics)))}"},
                {"name": "placement", "passed": placement_ok, "detail": placement_detail},
                {"name": "lec", "passed": lec_ok, "detail": lec_detail},
            ]
            sfinal_artifact = _observe_sfinal(design=self.config.design, benchmark_dir=self.config.benchmark_dir, output=output)
            return {
                "schema_version": "goalevolve.v2.stage-baseline.v1",
                "parent_id": parent.parent_id,
                "source_hash": parent.source_hash,
                "evaluation_mode": optimization_mode,
                "timing_recipe_id": timing_recipe_id,
                "ok": all(bool(check["passed"]) for check in checks),
                "metrics": metrics,
                "checks": checks,
                "artifacts": {
                    "openroad_binary": str(binary),
                    "evaluation_log": str(log),
                    "metrics_csv": str(metrics_csv),
                    "checkpoint_metrics": str(checkpoint_path),
                    "official_4of4_log": str(official_log),
                    "checkpoint_post_route_db": str(output / "post_route.odb"),
                    **sfinal_artifact,
                },
            }

    def evaluate(self, *, contract, parent: Parent, hypothesis: Hypothesis, student_id: str, workspace: Path, round_index: int) -> CandidateResult:
        source = workspace / "source"
        artifact = workspace.parent / "artifacts"
        artifact.mkdir(parents=True, exist_ok=True)
        configure_log = artifact / "configure.log"
        build_log = artifact / "build.log"
        # A candidate is a delta from the common parent, not from pristine
        # openroad-power.  Otherwise every later round appears to re-edit all
        # inherited source changes (including earlier timing work), which
        # corrupts edit-scope enforcement and mechanism attribution.
        parent_source = _workspace_parent_source(workspace) or self.config.source_seed
        diff = _source_diff(
            parent_source,
            source,
            allowed_roots=self.config.allowed_patch_roots,
        )
        commit = _tree_hash(source, allowed_roots=self.config.allowed_patch_roots)
        if not diff:
            return CandidateResult(student_id, hypothesis, {}, {}, [], "", commit, evaluation_error="no_source_change")
        # A stage scheduler can narrow the normal campaign-wide patch roots
        # to one executed mechanism.  Reject a cross-mechanism patch before
        # consuming a private build and official flow.
        scoped = self._with_preflight(
            CandidateResult(
                student_id,
                hypothesis,
                {},
                {},
                [],
                diff,
                commit,
                artifacts={"parent_source_for_delta": str(parent_source)},
            )
        )
        if scoped.evaluation_error:
            return scoped
        # A private Student build has no mutable artifacts in common with any
        # other Student.  Serializing it here made a four-Student round pay
        # four full OpenROAD build latencies before the first measurement.
        # Only the benchmark flow and official checker share the measurement
        # resource, so compile before acquiring that lock.
        try:
            binary = self._build(source, workspace, configure_log=configure_log, build_log=build_log)
            output = artifact / "contest_output"
            output.mkdir(parents=True, exist_ok=True)
            log = output / "evaluation.log"
            tcl = output / "evaluate.tcl"
            # A parent post-route ODB cannot be fed into a Tcl program that
            # then runs repair_design, repair_timing, and global_route again:
            # it changes the experiment from "one full flow of a descendant"
            # to a second pass over an already optimized placement.  Besides
            # being incomparable with the frozen baseline, that replay can
            # create a common QoR regression unrelated to the Student edit.
            # Parent checkpoint artifacts remain diagnosis evidence only; all
            # candidates execute the same full flow from the benchmark input.
            self._write_tcl(
                tcl=tcl,
                output=output,
                goal_tns_abs_ns=_goal_tns_abs_ns(contract),
                optimization_mode=hypothesis.evaluation_mode,
                timing_recipe_id=hypothesis.timing_recipe_id,
            )
        except Exception as exc:
            return self._with_preflight(CandidateResult(
                student_id,
                hypothesis,
                {},
                {},
                [],
                diff,
                commit,
                artifacts={
                    "candidate_source": str(source),
                    "configure_log": str(configure_log),
                    "build_log": str(build_log),
                },
                evaluation_error=f"{type(exc).__name__}:{exc}",
            ))

        with self._measurement_lock:
            try:
                runner = ResilientCommandRunner(self.config.execution_policy)
                flow = runner.run(command=[str(binary), "-exit", str(tcl)], cwd=output, output_log=log, environment=self.config.toolchain_environment)
                checkpoint_path = output / "checkpoint_metrics.json"
                atomic = _checkpoint_metrics(log)
                checkpoint_path.write_text(json.dumps(atomic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                tradeoff_path = output / "power_timing_cell_tradeoff.json"
                tradeoff_path.write_text(json.dumps(_power_timing_cell_tradeoff(output), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                metrics_csv = output / "metrics.csv"
                parsed = runner.run(command=["python3", str(OFFICIAL_PARSE_LOG), str(log), "--csv", str(metrics_csv)], cwd=output, environment=self.config.toolchain_environment)
                metrics = _read_metrics(metrics_csv) if parsed.ok and metrics_csv.is_file() else {}
                required = {spec.name for spec in contract.metrics}
                metrics_ok = required.issubset(metrics)
                checks = [
                    CheckResult("build", True, str(binary)),
                    CheckResult("flow", flow.ok, flow.resource_error or "official_openroad_flow"),
                    CheckResult("metrics", parsed.ok and metrics_ok, "official_parse_log" if metrics_ok else f"missing_metrics:{','.join(sorted(required - set(metrics)))}"),
                ]
                if not flow.ok or not metrics_ok:
                    checks.append(CheckResult("lec", False, "flow_or_metrics_failed"))
                    failure = "flow_or_metrics_failed"
                    if not flow.ok and flow.resource_error:
                        failure = f"{failure}:{flow.resource_error}"
                    return self._with_preflight(CandidateResult(student_id, hypothesis, metrics, {}, checks, diff, commit, artifacts={"candidate_source": str(source), "evaluation_log": str(log), "metrics_csv": str(metrics_csv), "checkpoint_metrics": str(checkpoint_path), "power_timing_cell_tradeoff": str(tradeoff_path), "checkpoint_post_route_db": str(output / "post_route.odb")}, evaluation_error=failure))
                placement_ok, placement_detail = _placement_legal(log)
                checks.append(CheckResult("placement", placement_ok, placement_detail))
                if not placement_ok:
                    checks.append(CheckResult("lec", False, "placement_failed"))
                    return self._with_preflight(CandidateResult(student_id, hypothesis, metrics, {}, checks, diff, commit, artifacts={"candidate_source": str(source), "evaluation_log": str(log), "metrics_csv": str(metrics_csv), "checkpoint_metrics": str(checkpoint_path), "power_timing_cell_tradeoff": str(tradeoff_path), "checkpoint_post_route_db": str(output / "post_route.odb")}, evaluation_error=placement_detail))
                lec_ok, lec_detail = official_four_check(
                    pre_opt=self.config.benchmark_dir,
                    post_opt=output,
                    output_log=output / "official_4of4.log",
                    policy=self.config.execution_policy,
                    environment=self.config.toolchain_environment,
                )
                checks.append(CheckResult("lec", lec_ok, lec_detail))
                observed_signals = tuple(
                    dict.fromkeys((*hypothesis.expected_signals, *hypothesis.activation_signals))
                )
                phase = _observed_phase_signals(log, observed_signals) if lec_ok else {}
                sfinal_artifact = _observe_sfinal(design=self.config.design, benchmark_dir=self.config.benchmark_dir, output=output)
                return self._with_preflight(CandidateResult(
                    student_id,
                    hypothesis,
                    metrics,
                    phase,
                    checks,
                    diff,
                    commit,
                    artifacts={"candidate_source": str(source), "evaluation_log": str(log), "metrics_csv": str(metrics_csv), "checkpoint_metrics": str(checkpoint_path), "power_timing_cell_tradeoff": str(tradeoff_path), "checkpoint_post_route_db": str(output / "post_route.odb"), "official_4of4_log": str(output / "official_4of4.log"), **sfinal_artifact},
                ))
            except Exception as exc:
                return self._with_preflight(CandidateResult(
                    student_id,
                    hypothesis,
                    {},
                    {},
                    [],
                    diff,
                    commit,
                    artifacts={
                        "candidate_source": str(source),
                        "configure_log": str(configure_log),
                        "build_log": str(build_log),
                    },
                    evaluation_error=f"{type(exc).__name__}:{exc}",
                ))

    def recover_completed_candidate(
        self,
        *,
        contract,
        parent: Parent,
        hypothesis: Hypothesis,
        student_id: str,
        workspace: Path,
        round_index: int,
    ) -> CandidateResult | None:
        """Reconstruct a terminal CandidateResult from immutable artifacts.

        This path performs no build, OpenROAD, parser, score, or LEC command.
        It is intentionally strict about terminal markers so a controller
        restart cannot mistake a partially written flow directory for an
        official result.  The source delta and tree hash are recomputed from
        the preserved Student tree; QoR and telemetry are parsed through the
        same readers as a live evaluation.
        """
        source = workspace / "source"
        artifact = workspace.parent / "artifacts"
        output = artifact / "contest_output"
        log = output / "evaluation.log"
        metrics_csv = output / "metrics.csv"
        official_log = output / "official_4of4.log"
        if not all(path.is_file() for path in (log, metrics_csv, official_log)):
            return None
        log_text = log.read_text(encoding="utf-8", errors="ignore")
        lec_text = official_log.read_text(encoding="utf-8", errors="ignore")
        # Both reports write their summary only after producing the exact
        # artifacts consumed by promotion.  Absence means the prior process
        # may still have been running or died mid-stage, so normal Student
        # repair/evaluation must resume instead of recovering it as terminal.
        if "[INFO] Flow running time:" not in log_text:
            return None
        if "SUMMARY:" not in lec_text or "RESULT:" not in lec_text:
            return None

        parent_source = _workspace_parent_source(workspace) or self.config.source_seed
        if not source.is_dir() or not parent_source.is_dir():
            return None
        diff = _source_diff(
            parent_source,
            source,
            allowed_roots=self.config.allowed_patch_roots,
        )
        commit = _tree_hash(source, allowed_roots=self.config.allowed_patch_roots)
        metrics = _read_metrics(metrics_csv)
        required = {spec.name for spec in contract.metrics}
        metrics_ok = required.issubset(metrics)

        binary = workspace / self.config.build_dir_name / "bin" / "openroad"
        build_ok = binary.is_file()
        required_flow_outputs = (
            output / "post_route.odb",
            output / f"{self.config.design}.def",
            output / f"{self.config.design}.v",
        )
        flow_ok = (
            "GOALEVOLVE_CHECKPOINT_END post_route" in log_text
            and all(path.is_file() for path in required_flow_outputs)
        )
        placement_ok, placement_detail = _placement_legal(log)
        lec_ok = (
            "SUMMARY: 4/4 checks passed" in lec_text
            and "RESULT: VALID" in lec_text
        )
        checks = [
            CheckResult("build", build_ok, str(binary) if build_ok else "recovery_binary_missing"),
            CheckResult("flow", flow_ok, "recovered_terminal_official_openroad_flow" if flow_ok else "recovery_flow_outputs_incomplete"),
            CheckResult("metrics", metrics_ok, "official_metrics_recovered" if metrics_ok else f"missing_metrics:{','.join(sorted(required - set(metrics)))}"),
            CheckResult("placement", placement_ok, placement_detail),
            CheckResult("lec", lec_ok, "official_4of4_pass_recovered" if lec_ok else "official_4of4_failed_recovered"),
        ]
        observed_signals = tuple(
            dict.fromkeys((*hypothesis.expected_signals, *hypothesis.activation_signals))
        )
        phase = _observed_phase_signals(log, observed_signals) if lec_ok else {}
        checkpoint_path = output / "checkpoint_metrics.json"
        tradeoff_path = output / "power_timing_cell_tradeoff.json"
        artifacts = {
            "candidate_source": str(source),
            "parent_source_for_delta": str(parent_source),
            "configure_log": str(artifact / "configure.log"),
            "build_log": str(artifact / "build.log"),
            "evaluation_log": str(log),
            "metrics_csv": str(metrics_csv),
            "checkpoint_metrics": str(checkpoint_path),
            "power_timing_cell_tradeoff": str(tradeoff_path),
            "checkpoint_post_route_db": str(output / "post_route.odb"),
            "official_4of4_log": str(official_log),
        }
        sfinal_path = output / "sfinal_observation.json"
        if sfinal_path.is_file():
            artifacts["sfinal_observation"] = str(sfinal_path)
        codex_root = artifact / "codex"
        for name in ("events.jsonl", "invocation.json", "last_message.md", "prompt.md", "stderr.log"):
            path = codex_root / name
            if path.is_file():
                artifacts[f"codex_{path.stem}"] = str(path)
        failures = [check.name for check in checks if not check.passed]
        evaluation_error = (
            "recovered_terminal_candidate_failed:" + ",".join(failures)
            if failures
            else None
        )
        return self._with_preflight(
            CandidateResult(
                student_id,
                hypothesis,
                metrics,
                phase,
                checks,
                diff,
                commit,
                artifacts=artifacts,
                evaluation_error=evaluation_error,
            )
        )

    def _build(self, source: Path, workspace: Path, *, configure_log: Path, build_log: Path) -> Path:
        build = workspace / self.config.build_dir_name
        runner = ResilientCommandRunner(self.config.execution_policy)
        cow_marker = build / ".goalevolve_relocated_cow"
        reused_seed = cow_marker.is_file()
        if not build.exists():
            reused_seed = self._seed_private_build(build, source=source)
            if reused_seed:
                cow_marker.write_text("relocated_cow_v1\n", encoding="utf-8")
        if reused_seed:
            # ``_relocate_cmake_state`` has already retargeted a known-good
            # build graph and preserved its original generated-state mtimes.
            # Running CMake here rewrites Makefile2/build.make/dependency
            # state at the current time, which forces a full OpenROAD rebuild
            # despite a one-file Student patch.  The Student contract forbids
            # CMake/build-system edits, so directly invoking the relocated
            # graph is both safe and the only path that preserves incrementality.
            configure_log.write_text("reused_relocated_cow_build: configure skipped\n", encoding="utf-8")
            compile_seed = self.config.build_seed_root or self.config.source_seed
            changed = self._changed_cpp_paths(
                seed=compile_seed,
                candidate=source,
                allowed_roots=self.config.allowed_patch_roots,
            )
            if changed:
                self._build_relocated_cow(
                    source=source,
                    build=build,
                    build_log=build_log,
                    changed=changed,
                )
            else:
                # A p0 baseline has no source delta. The relocated donor
                # binary is already the binary for that exact source surface;
                # relinking it would only add build noise and a full rebuild.
                build_log.write_text(
                    "reused_relocated_cow_build: no_source_delta; binary reused\n",
                    encoding="utf-8",
                )
        else:
            configure = runner.run(
                command=[
                    "cmake",
                    "-S",
                    str(source),
                    "-B",
                    str(build),
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DCMAKE_SUPPRESS_REGENERATION=ON",
                    *self.config.toolchain_cmake_args,
                ],
                cwd=workspace,
                output_log=configure_log,
                environment=self.config.toolchain_environment,
            )
            if not configure.ok:
                raise RuntimeError(f"candidate_configure_failed:{configure.resource_error or configure.returncode}")
        if not reused_seed:
            built = runner.run(command=["cmake", "--build", str(build), "--target", "openroad", "--parallel", str(self.config.build_jobs)], cwd=workspace, output_log=build_log, environment=self.config.toolchain_environment)
            if not built.ok:
                raise RuntimeError(f"candidate_build_failed:{built.resource_error or built.returncode}")
        binary = build / "bin" / "openroad"
        if not binary.is_file():
            raise RuntimeError("candidate_build_failed:openroad_binary_missing")
        return binary

    def _build_relocated_cow(
        self,
        *,
        source: Path,
        build: Path,
        build_log: Path,
        changed: tuple[Path, ...] | None = None,
    ) -> None:
        """Compile only objects affected by a Student patch, then relink.

        A relocated Unix-Makefiles tree can compile a named object correctly,
        but CMake's aggregate ``openroad`` target first runs every generated
        dependency target.  That refresh makes every object stale solely
        because the private source path differs.  The Student contract bans
        CMake edits, so we instead use the existing compile database and
        dependency files to build precisely the changed transitive objects.
        """
        # A promoted parent is source-only, while the COW object graph comes
        # from build_seed_root.  Every donor-to-parent delta must be rebuilt
        # alongside the Student delta.  Comparing only source_seed to the
        # candidate otherwise links a pristine donor binary plus one Student
        # object and yields baseline QoR under the candidate's provenance.
        compile_seed = self.config.build_seed_root or self.config.source_seed
        changed = changed if changed is not None else self._changed_cpp_paths(
            seed=compile_seed,
            candidate=source,
            allowed_roots=self.config.allowed_patch_roots,
        )
        objects = self._affected_objects(
            build=build,
            seed=compile_seed,
            candidate=source,
            # ``source_seed`` is the promoted parent snapshot.  A COW build
            # may instead originate from the configured build donor, so its
            # compile database can legitimately retain that donor's absolute
            # source paths.  Supply both identities when resolving an edited
            # source file to its object target.
            compile_seed=compile_seed,
            changed=changed,
        )
        if not objects:
            raise RuntimeError("candidate_build_failed:no_compilation_target_for_source_patch")
        runner = ResilientCommandRunner(self.config.execution_policy)
        reports = []
        object_paths = tuple(build / object_name for object_name in objects)
        object_mtimes = {path: path.stat().st_mtime_ns if path.is_file() else -1 for path in object_paths}
        # CMake's generated Makefile has short object aliases only in the
        # target's own binary directory.  Passing a full object path to the
        # top-level Makefile can return success without a recipe, which would
        # relink a stale object.  Group by that directory and invoke CMake's
        # local alias (for example ``RepairDesign.cc.o``) instead.
        for directory, targets in self._object_build_groups(build=build, objects=objects):
            self._retarget_selected_make_state(directory=directory, build=build, source=source)
            report = runner.run(
                # A COW-relocated Make graph can retain source/object mtimes
                # from the immutable seed.  Forcing only the explicitly
                # affected object aliases is both bounded and necessary: a
                # normal make may correctly return success while deciding
                # that an object whose source content changed during the same
                # timestamp tick is still current.  ``-B`` is deliberately
                # scoped to these aliases, never the aggregate OpenROAD
                # target, so unrelated source is not rebuilt.
                command=["make", "-C", str(directory), "-B", f"-j{self.config.build_jobs}", *targets],
                cwd=directory,
                environment=self.config.toolchain_environment,
            )
            reports.append(report)
            if not report.ok:
                self._write_build_reports(build_log, reports, objects)
                raise RuntimeError(f"candidate_build_failed:{report.resource_error or report.returncode}")
        stale = [str(path.relative_to(build)) for path in object_paths if not path.is_file() or path.stat().st_mtime_ns <= object_mtimes[path]]
        if stale:
            self._write_build_reports(build_log, reports, objects)
            raise RuntimeError(f"candidate_build_failed:incremental_objects_not_rebuilt:{','.join(stale)}")
        link_scripts = self._link_scripts_for_objects(build=build, objects=objects)
        for script in link_scripts:
            self._retarget_text_file(path=script, build=build, source=source)
            self._remove_stale_static_archive(link_script=script, cwd=script.parent.parent.parent)
            report = runner.run(
                command=["cmake", "-E", "cmake_link_script", str(script), "--verbose"],
                cwd=script.parent.parent.parent,
                environment=self.config.toolchain_environment,
            )
            reports.append(report)
            if not report.ok:
                self._write_build_reports(build_log, reports, objects)
                raise RuntimeError(f"candidate_build_failed:{report.resource_error or report.returncode}")
        executable_link = build / "src" / "CMakeFiles" / "openroad.dir" / "link.txt"
        self._retarget_text_file(path=executable_link, build=build, source=source)
        report = runner.run(
            command=["cmake", "-E", "cmake_link_script", str(executable_link), "--verbose"],
            cwd=executable_link.parent.parent.parent,
            environment=self.config.toolchain_environment,
        )
        reports.append(report)
        self._write_build_reports(build_log, reports, objects)
        if not report.ok:
            raise RuntimeError(f"candidate_build_failed:{report.resource_error or report.returncode}")

    @staticmethod
    def _changed_cpp_paths(
        *,
        seed: Path,
        candidate: Path,
        allowed_roots: tuple[str, ...] = (),
    ) -> tuple[Path, ...]:
        suffixes = {".cc", ".cpp", ".cxx", ".c", ".hh", ".hpp", ".h"}
        changed: list[Path] = []
        for path in _evolution_source_files(candidate, suffixes, allowed_roots=allowed_roots):
            relative = path.relative_to(candidate)
            if relative in _GENERATED_SOURCE_METADATA:
                continue
            baseline = seed / relative
            if not baseline.is_file() or baseline.read_bytes() != path.read_bytes():
                changed.append(path)
        return tuple(changed)

    @staticmethod
    def _affected_objects(
        *,
        build: Path,
        changed: tuple[Path, ...],
        seed: Path | None = None,
        candidate: Path | None = None,
        compile_seed: Path | None = None,
    ) -> tuple[str, ...]:
        database = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
        by_source = {
            str(Path(str(entry.get("file") or "")).resolve()): str(entry.get("output") or "")
            for entry in database
            if isinstance(entry, dict) and entry.get("output")
        }
        objects: set[str] = set()
        header_paths: list[str] = []
        for path in changed:
            output = by_source.get(str(path.resolve()))
            if output is None and seed is not None and candidate is not None:
                relative = path.relative_to(candidate)
                output = by_source.get(str((seed / relative).resolve()))
                if output is None and compile_seed is not None:
                    output = by_source.get(str((compile_seed / relative).resolve()))
            if output:
                objects.add(output)
            else:
                header_paths.append(str(path.resolve()))
                if seed is not None and candidate is not None:
                    relative = path.relative_to(candidate)
                    header_paths.append(str((seed / relative).resolve()))
                    if compile_seed is not None:
                        header_paths.append(str((compile_seed / relative).resolve()))
        if header_paths:
            for dependency in build.rglob("*.o.d"):
                try:
                    contents = dependency.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if any(header in contents for header in header_paths):
                    object_path = dependency.with_suffix("")
                    if object_path.is_file():
                        objects.add(str(object_path.relative_to(build)))
        return tuple(sorted(objects))

    def _retarget_selected_make_state(self, *, directory: Path, build: Path, source: Path) -> None:
        """Retarget only the Makefiles used by the selected object aliases.

        The directory-level Makefile is the recursion entry point generated by
        Unix Makefiles.  Leaving its old ``CMAKE_BINARY_DIR`` untouched makes
        ``make -C <private-dir>`` silently recurse into the seed build: make
        reports success, but the private object remains stale.  The adjacent
        target state alone is not sufficient.
        """
        self._retarget_text_file(path=directory / "Makefile", build=build, source=source)
        marker_dirs = [path for path in directory.glob("CMakeFiles/*.dir") if path.is_dir()]
        for marker in marker_dirs:
            for name in ("build.make", "flags.make"):
                self._retarget_text_file(path=marker / name, build=build, source=source)

    def _retarget_text_file(self, *, path: Path, build: Path, source: Path) -> None:
        if not path.is_file():
            return
        # ``source_seed`` may be a promoted snapshot without a build tree,
        # while the COW graph was copied from ``build_seed_root``.  Generated
        # Unix-Makefiles retain both donor paths.  Retargeting only paths
        # under source_seed makes a local ``make`` recurse into the donor
        # build and silently compile the donor source instead of the Student
        # source.  Replace every viable donor identity, longest first.
        build_seed = self.config.build_seed_root or self.config.source_seed
        old_builds = tuple(
            sorted(
                (
                    candidate.resolve()
                    for root in {self.config.source_seed, build_seed}
                    for candidate in (root / "build_power", root / "build")
                    if candidate.is_dir()
                ),
                key=lambda candidate: len(str(candidate)),
                reverse=True,
            )
        )
        old_sources = tuple(
            sorted(
                {self.config.source_seed.resolve(), build_seed.resolve()},
                key=lambda candidate: len(str(candidate)),
                reverse=True,
            )
        )
        try:
            original = path.stat()
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            rewritten = text
            for old_build in old_builds:
                rewritten = rewritten.replace(str(old_build), str(build.resolve()))
            for old_source in old_sources:
                rewritten = rewritten.replace(str(old_source), str(source.resolve()))
            if rewritten != text:
                path.write_text(rewritten, encoding="utf-8", errors="surrogateescape")
                os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        except OSError:
            return

    @staticmethod
    def _object_build_groups(*, build: Path, objects: tuple[str, ...]) -> tuple[tuple[Path, tuple[str, ...]], ...]:
        """Map CMake object paths to the local Makefile aliases that build them."""
        groups: dict[Path, list[str]] = {}
        for object_name in objects:
            object_path = build / object_name
            # Some generated CMake objects (notably SWIG wrappers) have a
            # nested ``CMakeFiles/<target>.dir/CMakeFiles/<target>.dir``
            # output path.  The innermost marker has no local Makefile; use
            # the nearest marker whose owning binary directory does.
            marker = next(
                (
                    parent
                    for parent in object_path.parents
                    if parent.name.endswith(".dir")
                    and parent.parent.name == "CMakeFiles"
                    and (parent.parent.parent / "Makefile").is_file()
                ),
                None,
            )
            if marker is None:
                raise RuntimeError(f"candidate_build_failed:unrecognized_cmake_object:{object_name}")
            directory = marker.parent.parent
            if not (directory / "Makefile").is_file():
                raise RuntimeError(f"candidate_build_failed:missing_object_makefile:{object_name}")
            # CMake exposes aliases relative to the target's ``*.dir``
            # directory.  Most Rsz objects happen to be flat, but GRT has
            # objects such as ``CMakeFiles/grt_lib.dir/src/GlobalRouter.cpp.o``
            # whose alias is ``src/GlobalRouter.cpp.o`` rather than the bare
            # basename.  Keep that relative component so both layouts invoke
            # an actual compile recipe.
            groups.setdefault(directory, []).append(str(object_path.relative_to(marker)))
        return tuple((directory, tuple(sorted(targets))) for directory, targets in sorted(groups.items()))

    @staticmethod
    def _link_scripts_for_objects(*, build: Path, objects: tuple[str, ...]) -> tuple[Path, ...]:
        scripts: set[Path] = set()
        for object_name in objects:
            object_path = build / object_name
            marker = next(
                (
                    parent
                    for parent in object_path.parents
                    if parent.name.endswith(".dir")
                    and parent.parent.name == "CMakeFiles"
                    and (parent.parent.parent / "Makefile").is_file()
                ),
                None,
            )
            if marker is None:
                continue
            link = marker / "link.txt"
            if link.is_file():
                scripts.add(link)
        return tuple(sorted(scripts))

    @staticmethod
    def _remove_stale_static_archive(*, link_script: Path, cwd: Path) -> None:
        """Prevent ``ar qc`` from retaining a seed object's old duplicate.

        The Unix-Makefiles link scripts use ``ar qc`` rather than replacement
        mode.  That is harmless in a clean build but a COW copy already owns
        every archive member: the new object is appended and the linker can
        resolve the first, stale member.  Only remove a local archive produced
        by the exact static-library link script; shared/executable links are
        never touched.
        """
        try:
            first = next(
                line.strip()
                for line in link_script.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip()
            )
            words = shlex.split(first)
        except (OSError, StopIteration, ValueError):
            return
        if len(words) < 3 or Path(words[0]).name != "ar" or "q" not in words[1]:
            return
        archive = Path(words[2])
        if archive.is_absolute() or archive.suffix != ".a":
            return
        target = (cwd / archive).resolve()
        if target.parent != cwd.resolve():
            return
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _write_build_reports(path: Path, reports, objects: tuple[str, ...]) -> None:
        payload = {
            "mode": "relocated_cow_direct_objects",
            "objects": list(objects),
            "reports": [report.to_dict() for report in reports],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _seed_private_build(self, build: Path, *, source: Path) -> bool:
        """Use a copy-on-write seed only when the filesystem supports reflinks.

        A normal recursive copy of a multi-GB OpenROAD build would make four
        Students slower than clean CMake builds. ``--reflink=always`` makes the
        optimization explicit and safely falls back to an empty private build.
        """
        # The source parent can be a snapshot with build directories omitted.
        # In that case an explicitly configured immutable donor preserves the
        # build graph while all source provenance remains tied to source_seed.
        build_seed_root = self.config.build_seed_root or self.config.source_seed
        for name in ("build_power", "build"):
            seed = build_seed_root / name
            if not (seed / "CMakeCache.txt").is_file():
                continue
            seed_source = self._cache_path(seed / "CMakeCache.txt", "CMAKE_HOME_DIRECTORY")
            if seed_source != build_seed_root.resolve():
                continue
            copied = subprocess.run(["cp", "-a", "--reflink=always", str(seed), str(build)], text=True, capture_output=True, check=False)
            if copied.returncode == 0:
                if self._relocate_cmake_state(build=build, old_source=seed_source, old_build=seed.resolve(), new_source=source.resolve()):
                    print(f"[GoalEvolve][build] reflink_relocated_seed={seed.name}", flush=True)
                    return True
                # A malformed/unexpected cache must never be used just to
                # save time. The safe fallback is a fresh CMake graph.
                self._reset_relocated_cmake_state(build)
                print(f"[GoalEvolve][build] reflink_seed_reset={seed.name}", flush=True)
                return False
            if build.exists():
                shutil.rmtree(build)
        build.mkdir(parents=True, exist_ok=True)
        return False

    @staticmethod
    def _preserve_reused_object_freshness(build: Path) -> None:
        """Keep CMake's relocated path metadata from invalidating every object.

        The first configure in a private build rewrites ``flags.make`` and
        ``compiler_depend.ts`` because their absolute paths changed.  GNU Make
        lists both as direct prerequisites of every object, so their new mtime
        would otherwise force an unrelated full rebuild.  The compiler flags
        differ only in private source/build path prefixes; after relocation the
        existing objects remain semantically valid.  Source/header edits still
        have newer mtimes and continue to trigger their normal incremental
        rebuild through the dependency files.
        """
        for flags in build.rglob("flags.make"):
            target_dir = flags.parent
            objects = [item for item in target_dir.rglob("*.o") if item.is_file()]
            if not objects:
                continue
            stamp = min(item.stat().st_mtime_ns for item in objects) - 1_000_000_000
            for state in (flags, target_dir / "compiler_depend.ts"):
                if state.is_file():
                    os.utime(state, ns=(stamp, stamp))

    @staticmethod
    def _cache_path(cache: Path, key: str) -> Path | None:
        try:
            for line in cache.read_text(encoding="utf-8", errors="ignore").splitlines():
                prefix = f"{key}:INTERNAL="
                if line.startswith(prefix):
                    return Path(line[len(prefix) :]).resolve()
        except OSError:
            pass
        return None

    @staticmethod
    def _relocate_cmake_state(*, build: Path, old_source: Path, old_build: Path, new_source: Path) -> bool:
        """Retarget a reflinked matching CMake build without discarding objects.

        CMake records absolute source/build paths in its cache, generated make
        rules and dependency files. Rewriting those text references lets the
        private COW tree retain unchanged object files, so each Student builds
        its policy edit rather than recompiling all of OpenROAD. This is safe
        only when the seed cache explicitly names the configured source seed.
        """
        # Only CMakeCache needs eager relocation.  Source-specific Makefiles
        # and link scripts are retargeted later, after the candidate diff has
        # identified the exact object aliases.  Scanning every generated .d
        # file in a multi-GB COW tree made one Student spend minutes in I/O.
        try:
            cache = build / "CMakeCache.txt"
            original = cache.stat()
            text = cache.read_text(encoding="utf-8", errors="surrogateescape")
            rewritten = text.replace(str(old_build), str(build.resolve())).replace(str(old_source), str(new_source))
            if rewritten != text:
                cache.write_text(rewritten, encoding="utf-8", errors="surrogateescape")
                os.utime(cache, ns=(original.st_atime_ns, original.st_mtime_ns))
            # These root-level files are tiny but are consulted before local
            # aliases.  Retarget them eagerly without walking the build tree.
            for relative in (Path("CMakeFiles/rules.make"), Path("CMakeFiles/Makefile2"), Path("Makefile")):
                path = build / relative
                if not path.is_file():
                    continue
                original = path.stat()
                text = path.read_text(encoding="utf-8", errors="surrogateescape")
                rewritten = text.replace(str(old_build), str(build.resolve())).replace(str(old_source), str(new_source))
                if rewritten != text:
                    path.write_text(rewritten, encoding="utf-8", errors="surrogateescape")
                    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        except OSError:
            return False
        cache = Contest2026OpenROADEvaluator._cache_path(build / "CMakeCache.txt", "CMAKE_HOME_DIRECTORY")
        return cache == new_source

    @staticmethod
    def _cmake_text_state(path: Path) -> bool:
        """Whether a copied build file is generated CMake/Make text state."""
        if path.name in {
            "Makefile",
            "Makefile2",
            "CMakeCache.txt",
            "cmake.check_cache",
            "TargetDirectories.txt",
            "progress.marks",
            "DependInfo.cmake",
            "link.txt",
            "flags.make",
            "compiler_depend.internal",
        }:
            return True
        return path.suffix in {".cmake", ".make", ".d", ".json"}

    @staticmethod
    def _reset_relocated_cmake_state(build: Path) -> None:
        """Remove CMake's absolute-path state after relocating a COW seed.

        A copied OpenROAD build cache names the original source and build
        directories. Reusing it verbatim makes every Student fail at configure
        before its edit is compiled. Object/library files remain available as
        copy-on-write inputs, while CMake regenerates source-location-specific
        rules for the private tree.
        """
        cache = build / "CMakeCache.txt"
        if cache.exists():
            cache.unlink()
        state = build / "CMakeFiles"
        if state.exists():
            shutil.rmtree(state)

    def _with_preflight(self, candidate: CandidateResult) -> CandidateResult:
        report = preflight_candidate(
            candidate,
            allowed_patch_roots=self.config.allowed_patch_roots,
            allowed_patch_paths=candidate.hypothesis.allowed_patch_paths,
            require_cpp_patch=self.config.require_cpp_patch,
        )
        candidate.artifacts["preflight"] = json.dumps(report.to_dict(), sort_keys=True)
        if not report.ok:
            candidate.evaluation_error = candidate.evaluation_error or ";".join(report.violations)
        return candidate

    def _write_tcl(
        self,
        *,
        tcl: Path,
        output: Path,
        parent_checkpoint: Path | None = None,
        goal_tns_abs_ns: float | None = None,
        optimization_mode: str = "timing_only",
        timing_recipe_id: str = "legacy_setup",
    ) -> None:
        benchmark = self.config.benchmark_dir
        def_file = _single(benchmark, ("*.def", "*.def.gz"))
        verilog = _single(benchmark, ("*.v",))
        sdc = _single(benchmark, ("*.sdc",))
        lef_files = sorted((self.config.benchmark_root.parent / "asap7" / "lef").glob("*.lef"))
        lib_files = sorted((self.config.benchmark_root.parent / "asap7" / "lib").glob("*.lib"))
        if not lef_files or not lib_files:
            raise FileNotFoundError("contest ASAP7 LEF/lib assets are missing")
        rc_file = self.config.benchmark_root.parent / "asap7" / "setRC.tcl"
        if not rc_file.is_file():
            raise FileNotFoundError("contest ASAP7 RC setup is missing")
        def checkpoint(stage: str) -> list[str]:
            return [
                f"puts \"GOALEVOLVE_CHECKPOINT_BEGIN {stage}\"",
                f"puts [format \"GOALEVOLVE_CHECKPOINT_METRIC {stage} tns_abs_ns %.12g\" [total_negative_slack -max]]",
                f"puts [format \"GOALEVOLVE_CHECKPOINT_METRIC {stage} wns_abs_ns %.12g\" [worst_slack -max]]",
                "report_power -digits 12",
                f"write_verilog {_escape_tcl(output / (stage + '.v'))}",
                f"write_db {_escape_tcl(output / (stage + '.odb'))}",
                f"puts \"GOALEVOLVE_CHECKPOINT_END {stage}\"",
            ]

        design_load = [
            f"read_def {_escape_tcl(def_file)}",
            f"read_verilog {_escape_tcl(verilog)}",
        ] if parent_checkpoint is None else [f"read_db {_escape_tcl(parent_checkpoint)}"]
        power_command = [
            "repair_power"
            f" -phase {self.config.power_reclaim_phase}"
            f" -proportion {self.config.power_reclaim_proportion_percent:g}"
            + ("" if self.config.power_reclaim_max_moves <= 0 else f" -max_moves {self.config.power_reclaim_max_moves}")
        ]
        power_environment = [
            "set ::env(RSZ_POWER_STAGE_TNS_CEILING_S) "
            f"{self.config.power_stage_tns_ceiling_ns * 1.0e-9:.12g}"
        ]
        recipe = timing_recipe(timing_recipe_id)
        timing_command = recipe.repair_timing_command()
        recipe_environment = list(recipe.environment_commands())
        # RMP's delay mode already owns a journal-like snapshot/restore and
        # can trial ABC modes with real STA.  The controller supplies a
        # deliberately tiny, deterministic bracket: one cloud, one accepted
        # cloud, then a normal timing cleanup.  This is not a user Tcl sweep;
        # the same sequence is applied to a no-diff recipe baseline.
        # ABC's read_lib is not additive: a later library replaces the former.
        # Build one evaluation-local standard-cell library for RMP instead of
        # pretending a comma list is a multi-library ABC input.  Normal
        # OpenROAD loading above deliberately still reads every Liberty file.
        rmp_liberty = output / "rmp_standard_cells.lib"
        if recipe.insert_rmp_delay_restructure or recipe.insert_rmp_area_restructure:
            rmp_liberty = _write_rmp_combined_liberty(lib_files, output)
        rmp_workdir = output / "rmp_delay_restructure"
        rmp_delay_command = [
            f"file mkdir {_escape_tcl(rmp_workdir)}",
            f"set ::env(RMP_MAX_TRIED_CLOUDS) {recipe.rmp_max_tried_clouds}",
            "set ::env(RMP_MAX_ACCEPTED_CLOUDS) 1",
            f"set ::env(RMP_MAX_CLOUDS) {recipe.rmp_max_tried_clouds}",
            f"set ::env(RMP_ENDPOINT_PATH_COUNT) {recipe.rmp_endpoint_path_count}",
            "set ::env(RMP_UNIQUE_ENDPOINTS) 1",
            "set ::env(RMP_SKIP_DUPLICATE_CLOUDS) 1",
            *(
                ("set ::env(RMP_UNION_ENDPOINT_PATHS) 1",)
                if recipe.rmp_union_endpoint_paths
                else ()
            ),
            *(
                (
                    f"set ::env(RMP_EXPAND_SIDE_FANIN_LEVELS) {recipe.rmp_expand_side_fanin_levels}",
                    f"set ::env(RMP_EXPAND_SIDE_FANIN_MAX_ADD) {recipe.rmp_expand_side_fanin_max_add}",
                )
                if recipe.rmp_expand_side_fanin_levels > 0
                else ()
            ),
            *(
                ("set ::env(RMP_PATH_CONE_ONLY) 1",)
                if recipe.rmp_path_cone_only
                else ()
            ),
            "set ::env(RMP_STA_SELECT_BEST_MODE) 1",
            # Reject neutral/worse resynthesis outcomes; this preserves the
            # two already-met power targets while seeking timing recovery.
            "set ::env(RMP_GUARD_MIN_TNS_IMPROVE_NS) 0.001",
            "set ::env(RMP_GUARD_MIN_WNS_IMPROVE_NS) 0.0",
            "set ::env(RMP_TIMING_TELEMETRY) 1",
            "restructure"
            f" -liberty_file {_escape_tcl(rmp_liberty)}"
            " -target timing -slack_threshold 0 -depth_threshold 16"
            f" -work_dir {_escape_tcl(rmp_workdir)}",
            *checkpoint("post_rmp_restructure"),
        ]
        rmp_area_workdir = output / "rmp_area_restructure"
        rmp_area_command = [
            f"file mkdir {_escape_tcl(rmp_area_workdir)}",
            "set ::env(RMP_MAX_TRIED_CLOUDS) 4",
            "set ::env(RMP_MAX_ACCEPTED_CLOUDS) 1",
            "set ::env(RMP_MAX_CLOUDS) 4",
            "set ::env(RMP_UNIQUE_ENDPOINTS) 1",
            "set ::env(RMP_SKIP_DUPLICATE_CLOUDS) 1",
            "set ::env(RMP_AREA_TELEMETRY) 1",
            "restructure"
            f" -liberty_file {_escape_tcl(rmp_liberty)}"
            " -target area -slack_threshold 0 -depth_threshold 16"
            f" -work_dir {_escape_tcl(rmp_area_workdir)}",
            *checkpoint("post_rmp_area_restructure"),
        ]
        mid_power_command = [
            "repair_power -phase mid_area_reclaim -proportion 15 -max_moves 300",
            *checkpoint("post_repair_power_mid"),
        ]
        if optimization_mode == "power_only":
            optimization_steps = [
                *power_environment,
                *power_command,
                *checkpoint("post_repair_power"),
                *(rmp_area_command if recipe.insert_rmp_area_restructure else []),
            ]
        elif optimization_mode == "power_then_timing":
            optimization_steps = [
                *power_environment,
                *power_command,
                *checkpoint("post_repair_power"),
                *([] if goal_tns_abs_ns is None else [
                    f"set ::env(RSZ_GOAL_TNS_ABS_S) {goal_tns_abs_ns * 1.0e-9:.12g}"
                ]),
                *recipe_environment,
                timing_command,
                *checkpoint("post_repair_timing"),
            ]
        elif optimization_mode == "timing_only":
            optimization_steps = [
                *([] if goal_tns_abs_ns is None else [
                    f"set ::env(RSZ_GOAL_TNS_ABS_S) {goal_tns_abs_ns * 1.0e-9:.12g}"
                ]),
                *recipe_environment,
                timing_command,
                *checkpoint("post_repair_timing"),
            ]
        else:
            raise ValueError(f"unknown candidate optimization mode: {optimization_mode}")
        if optimization_mode == "power_then_timing" and recipe.insert_mid_power:
            optimization_steps = [
                *power_environment,
                *power_command,
                *checkpoint("post_repair_power"),
                *([] if goal_tns_abs_ns is None else [
                    f"set ::env(RSZ_GOAL_TNS_ABS_S) {goal_tns_abs_ns * 1.0e-9:.12g}"
                ]),
                *recipe_environment,
                timing_command,
                *checkpoint("post_repair_timing_pre_mid_power"),
                *mid_power_command,
                timing_command,
                *checkpoint("post_repair_timing"),
            ]
        elif optimization_mode == "power_then_timing" and recipe.insert_rmp_delay_restructure:
            goal_command = [] if goal_tns_abs_ns is None else [
                f"set ::env(RSZ_GOAL_TNS_ABS_S) {goal_tns_abs_ns * 1.0e-9:.12g}"
            ]
            if recipe.rmp_before_timing:
                optimization_steps = [
                    *power_environment,
                    *power_command,
                    *checkpoint("post_repair_power"),
                    *goal_command,
                    *recipe_environment,
                    *rmp_delay_command,
                    *checkpoint("post_rmp_restructure_pre_timing"),
                    timing_command,
                    *checkpoint("post_repair_timing"),
                ]
            else:
                optimization_steps = [
                    *power_environment,
                    *power_command,
                    *checkpoint("post_repair_power"),
                    *goal_command,
                    *recipe_environment,
                    timing_command,
                    *checkpoint("post_repair_timing_pre_rmp"),
                    *rmp_delay_command,
                    timing_command,
                    *checkpoint("post_repair_timing"),
                ]
        lines = [
            "set start [clock seconds]",
            *[f"read_lef {_escape_tcl(path)}" for path in lef_files],
            *[f"read_liberty {_escape_tcl(path)}" for path in lib_files],
            *design_load,
            f"read_sdc {_escape_tcl(sdc)}",
            "set_ideal_network [all_clocks]",
            f"source {_escape_tcl(rc_file)}",
            # `total_negative_slack` and `worst_slack` use the current OpenSTA
            # display unit.  Set it before the first checkpoint as well as
            # before repair, otherwise `pre_repair` is emitted in ps while all
            # later checkpoint values are ns and Teacher diagnosis invents a
            # 1000x upstream timing regression.
            "set_cmd_units -time ns -capacitance pF -current mA -voltage V -resistance kOhm -distance um -power mW",
            "set_units -power mW",
            "estimate_parasitics -placement",
            *checkpoint("pre_repair"),
            "set rsz_start [clock seconds]",
            "repair_design",
            *checkpoint("post_repair_design"),
            *optimization_steps,
            "set rsz_end [clock seconds]",
            "puts \"\\[INFO\\] OR RSZ running time:   [expr {$rsz_end - $rsz_start}] seconds\"",
            # ``check_placement`` reports violations but does not necessarily
            # throw Tcl.  Always legalize before the one authoritative check:
            # an initial failing probe would otherwise remain in the log and
            # could be contradicted by a fabricated "legal" marker later.
            # The controller owns the complete, fixed detailed-placement
            # sequence.  Post-placement QoR is measured only after it has
            # completed and placement parasitics have been re-estimated.
            "set_placement_padding -global -left 0 -right 0",
            "detailed_placement",
            "improve_placement -max_displacement {5 1}",
            "optimize_mirroring",
            "check_placement -verbose",
            # Follow the official evaluation protocol: placement RC is
            # estimated before global-routing layers are configured; the
            # M2--M9 (or design-specific) setup belongs to global routing.
            *_fresh_stage_qor_tcl(parasitics_command="estimate_parasitics -placement"),
            *checkpoint("post_placement"),
            f"write_def {_escape_tcl(output / (self.config.design + '.def'))}",
            f"write_verilog {_escape_tcl(output / (self.config.design + '.v'))}",
            "if {[info exists route_signal_layers]} { set signal_layers $route_signal_layers } else { set signal_layers M2-M9 }",
            "if {[info exists route_clock_layers]} { set clock_layers $route_clock_layers } else { set clock_layers M2-M9 }",
            "set_routing_layers -signal $signal_layers -clock $clock_layers",
            "global_route -skip_large_fanout_nets 300 -allow_congestion -congestion_iterations 50",
            *_fresh_stage_qor_tcl(parasitics_command="estimate_parasitics -global_routing"),
            *checkpoint("post_route"),
            "puts \"===== METRICS =====\"",
            f"puts \"design:                 {self.config.design}\"",
            "puts [format \"total_insts:            %d\" [llength [get_cells *]]]",
            "puts \"Placement legalized.\"",
            "report_units",
            "report_tns",
            "report_wns -digits 4",
            "report_power -digits 12",
            "report_check_types -max_slew -violators",
            "report_check_types -max_capacitance -violators",
            "report_check_types -max_fanout -violators",
            "puts \"\\[INFO\\] Flow running time:   [expr {[clock seconds] - $start}] seconds\"",
            f"source {_escape_tcl(OFFICIAL_UTILS_TCL)}",
            f"write_node_and_net_files {_escape_tcl(output / 'node.csv')} {_escape_tcl(output / 'nets.csv')}",
            "exit",
        ]
        tcl.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _observe_sfinal(*, design: str, benchmark_dir: Path, output: Path) -> dict[str, str]:
    """Persist and print Sfinal only as a post-evaluation comparison observer."""
    try:
        report = observe_sfinal(design=design, benchmark_dir=benchmark_dir, candidate_dir=output)
        score = dict(report["score"])
        print(f"[GoalEvolve][observer][Sfinal] design={design} Sfinal={float(score['Sfinal']):.12g}", flush=True)
        return {"sfinal_observation": str(output / "sfinal_observation.json")}
    except Exception as exc:
        detail = f"{type(exc).__name__}:{exc}"
        print(f"[GoalEvolve][observer][Sfinal] design={design} unavailable={detail}", flush=True)
        return {"sfinal_observation_error": detail}
