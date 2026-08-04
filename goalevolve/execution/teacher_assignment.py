"""Controller admission for Teacher-authored evolution mechanisms.

The Teacher authors the mechanism.  This module deliberately does not rank or
invent one: it turns a bounded Markdown assignment into an executable
``Hypothesis`` only after checking the local source snapshot, EPD references,
and Explorer novelty.  The same checks are kept outside the Teacher prompt so
they remain auditable and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..core.models import Hypothesis
from ..core.io import sha256_file, sha256_json
from ..planning.repository_graph import RepositoryGraph, RepositoryGraphIndex
from ..planning.timing_recovery import (
    recipe_for_source_hooks,
    recipe_is_compatible_with_source_hooks,
    recipes_for_students,
    teacher_selectable_recipe_ids,
)


_CPP_SUFFIXES = frozenset({".cc", ".cpp", ".cxx", ".hh", ".hpp", ".h"})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WORDS = re.compile(r"[a-z0-9_]+")
POWER_ONLY_EXECUTION_DIRECT_FILES = frozenset({
    "src/rsz/src/policy/RepairPowerPolicy.cc",
})
POWER_ONLY_EXECUTION_ENTRY_SYMBOLS = {
    "src/rsz/src/Resizer.cc": frozenset({"rsz::Resizer::repairPower"}),
    "src/rsz/src/Optimizer.cc": frozenset({"rsz::Optimizer::makePolicyForPhase"}),
    "src/rsz/src/policy/RepairPowerPolicy.cc": frozenset({
        "rsz::RepairPowerPolicy::iterate",
    }),
}
RMP_AREA_EXECUTION_DIRECT_FILES = frozenset({"src/rmp/src/Restructure.cpp"})
RMP_AREA_EXECUTION_ENTRY_SYMBOLS = {
    "src/rmp/src/Restructure.cpp": frozenset({
        "Restructure::runABC",
        "rmp::Restructure::runABC",
    }),
}


@dataclass(frozen=True)
class AssignmentMaterialization:
    hypotheses: tuple[Hypothesis, ...]
    errors: tuple[str, ...]


def build_role_templates(
    *,
    student_ids: Sequence[str],
    round_index: int,
    decision_context: Mapping[str, object],
    portfolio: Mapping[str, object],
    suspend_explorers: bool,
) -> tuple[Hypothesis, ...]:
    """Create role envelopes without deciding any source mechanism.

    Integrator and Enhancer are scheduled only when completed EPD evidence
    makes those roles meaningful.  When neither EPD role is available, all
    configured Student slots become independent Explorers so a fresh campaign
    can use its full evaluation budget.  A bottleneck transition may suspend
    exploration only when it leaves an eligible EPD cleanup role to execute.
    """
    ids = tuple(str(student_id) for student_id in student_ids if str(student_id))
    integration = tuple(
        tuple(str(record_id) for record_id in list(pair or ()) if str(record_id))
        for pair in list(portfolio.get("integration_candidates") or ())
        if len(tuple(str(record_id) for record_id in list(pair or ()) if str(record_id))) >= 2
    )
    enhancement = tuple(
        str(record_id)
        for record_id in list(portfolio.get("enhancement_candidates") or ())
        if str(record_id)
    )
    roles: list[tuple[str, str, tuple[dict[str, object], ...]]] = []
    has_epd_role = bool(integration or enhancement)
    explorer_slots = len(ids) if not has_epd_role else (0 if suspend_explorers else 2)
    roles.extend(("explorer", "fresh_exploration", ()) for _ in range(explorer_slots))
    if integration:
        roles.append(
            (
                "integrator",
                "epd_integration",
                tuple({"epd_record_ids": pair} for pair in integration),
            )
        )
    if enhancement:
        roles.append(
            (
                "enhancer",
                "epd_enhancement",
                tuple({"epd_record_ids": (record_id,)} for record_id in enhancement),
            )
        )
    recipes = {
        str(student_id): str(recipe)
        for student_id, recipe in dict(
            decision_context.get("timing_recipe_ids") or recipes_for_students(ids)
        ).items()
    }
    mode = str(decision_context.get("evaluation_mode") or "timing_only")
    stage = str(decision_context.get("stage") or "")
    templates: list[Hypothesis] = []
    for position, (student_id, (role, role_mode, options)) in enumerate(zip(ids, roles, strict=False), start=1):
        recipe_id = str(
            decision_context.get("execution_champion_recipe_id")
            if stage == "adaptive_tradeoff" and decision_context.get("execution_champion_recipe_id")
            else recipes.get(student_id, "legacy_setup")
        )
        templates.append(
            Hypothesis(
                hypothesis_id=f"r{round_index:03d}_{student_id}_{role}",
                mechanism_family=f"teacher_{role}",
                claim="Teacher-authored mechanism pending Markdown assignment.",
                source_hooks=(),
                expected_signals=(),
                retrieval_ids=(),
                novelty_key=f"r{round_index}:{student_id}:{role}",
                evaluation_mode=mode,
                timing_recipe_id=recipe_id,
                student_role=role,
                role_mode=role_mode,
                student_id=student_id,
                candidate_options=options,
            )
        )
    return tuple(templates)


def source_structure_index(
    *,
    source_root: Path,
    allowed_patch_roots: Sequence[str],
    max_symbols_per_file: int = 24,
    repository_graph: RepositoryGraph | None = None,
) -> dict[str, dict[str, object]]:
    """Return the legacy compact shape using AST-derived graph facts.

    The public function name remains for callers outside the engine.  New
    execution passes the already-built P0-rooted parent graph so the source is
    parsed once per round rather than falling back to regex discovery.
    """
    graph = repository_graph or RepositoryGraphIndex(state_root=source_root.parent).build_parent(
        source_root=source_root,
        source_hash=_compatibility_source_hash(source_root, allowed_patch_roots),
        allowed_patch_roots=allowed_patch_roots,
    )
    allowed = tuple(root.strip("/") for root in allowed_patch_roots if root.strip("/"))
    files: dict[str, dict[str, object]] = {}
    for path, source_file in graph.files.items():
        if allowed and not any(path == root or path.startswith(root + "/") for root in allowed):
            continue
        files[path] = {
            "symbols": [
                graph.symbols[symbol_id].qualified_name
                for symbol_id in source_file.symbol_ids[:max_symbols_per_file]
                if symbol_id in graph.symbols
            ],
            "existing_metric_signals": sorted(
                {
                    signal
                    for symbol_id in source_file.symbol_ids
                    if symbol_id in graph.symbols
                    for signal in graph.symbols[symbol_id].metric_signals
                }
            )[:max_symbols_per_file],
        }
    grouped: dict[str, list[tuple[str, dict[str, object]]]] = {}
    for relative, metadata in sorted(files.items()):
        group = next(
            (root.strip("/") for root in allowed_patch_roots if relative.startswith(root.strip("/") + "/")),
            relative.split("/", 1)[0],
        )
        grouped.setdefault(group, []).append((relative, metadata))
    compact: dict[str, dict[str, object]] = {}
    for group, entries in sorted(grouped.items()):
        selected = entries[:40]
        compact[group] = {
            "indexed_file_count": len(entries),
            "sampled_files": {
                relative: metadata for relative, metadata in selected
            },
            "truncated": len(entries) > len(selected),
        }
    return compact


def _compatibility_source_hash(source_root: Path, allowed_patch_roots: Sequence[str]) -> str:
    """Content identity for an external caller of the legacy index API."""

    records: list[tuple[str, str]] = []
    for configured_root in allowed_patch_roots:
        root = source_root / configured_root.strip("/")
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in _CPP_SUFFIXES:
                records.append((path.relative_to(source_root).as_posix(), sha256_file(path)))
    return sha256_json(records)


def materialize_teacher_assignments(
    *,
    assignments: Sequence[Mapping[str, object]],
    evolution_ideas: Sequence[Mapping[str, object]],
    templates: Sequence[Hypothesis],
    source_root: Path,
    allowed_patch_roots: Sequence[str],
    historical_ideas: Sequence[Mapping[str, object]],
    paper_card_ids: Sequence[str] = (),
    teacher_context: Mapping[str, object] | None = None,
    repository_graph: RepositoryGraph | None = None,
    explorer_retrieval_audit: Mapping[str, object] | None = None,
) -> AssignmentMaterialization:
    """Validate Markdown assignments and construct executable hypotheses.

    Explorer novelty is intentionally stricter than EPD roles: a fresh idea
    cannot rerun an already invalid, validated, or promising mechanism.  An
    Integrator/Enhancer is expected to revisit EPD evidence, so it is checked
    for valid references and source grounding but never rejected merely for
    historical resemblance.
    """
    rows = {
        str(row.get("student_id") or "").strip(): row
        for row in assignments
        if isinstance(row, Mapping) and str(row.get("student_id") or "").strip()
    }
    ideas = {
        str(idea.get("reference") or "").strip(): idea
        for idea in evolution_ideas
        if isinstance(idea, Mapping) and str(idea.get("reference") or "").strip()
    }
    normalized_history = tuple(
        row
        for row in historical_ideas
        if str(row.get("status") or "").lower() in {"invalid", "validated", "promising"}
    )
    errors: list[str] = []
    result: list[Hypothesis] = []
    used_explorer_keys: set[str] = set()
    context = dict(teacher_context or {})
    all_teacher_ideas = tuple(
        str(idea.get("idea") or "").strip()
        for idea in evolution_ideas
        if str(idea.get("idea") or "").strip()
    )

    for template in templates:
        student_id = template.student_id
        row = rows.get(student_id)
        if row is None:
            errors.append(f"missing_assignment:{student_id}")
            continue
        role = str(row.get("role") or "").lower()
        if role != template.student_role:
            errors.append(f"assignment_role_mismatch:{student_id}:{template.student_role}")
            continue
        claim = str(row.get("claim") or "").strip()
        rationale = str(row.get("selection_rationale") or "").strip()
        hooks = _source_hook_items(row.get("source_hooks"))
        signals = _items(row.get("expected_signals"))
        declared_activation = _items(row.get("activation_signals"))
        evidence = _source_evidence_items(row.get("source_evidence"))
        falsification = str(row.get("falsification_condition") or "").strip()
        if not claim or not rationale or not hooks or not signals or not evidence or not falsification:
            errors.append(f"incomplete_assignment:{student_id}")
            continue
        source_errors = _source_admission_errors(
            source_root=source_root,
            hooks=hooks,
            source_evidence=evidence,
            allowed_patch_roots=allowed_patch_roots,
            repository_graph=repository_graph,
        )
        if source_errors:
            errors.extend(f"{student_id}:{error}" for error in source_errors)
            continue
        invalid_signals = [signal for signal in signals if not _IDENTIFIER.fullmatch(signal)]
        if invalid_signals:
            errors.append(f"invalid_expected_signal:{student_id}:{invalid_signals[0]}")
            continue
        invalid_activation = [signal for signal in declared_activation if not _IDENTIFIER.fullmatch(signal)]
        if invalid_activation:
            errors.append(f"invalid_activation_signal:{student_id}:{invalid_activation[0]}")
            continue
        if declared_activation and not set(declared_activation).issubset(signals):
            errors.append(f"activation_signal_not_expected:{student_id}")
            continue
        idea_reference = str(row.get("idea_reference") or "").strip()
        linked_idea = ideas.get(idea_reference)
        internal_cpp_scheduling_suggestion = str(
            row.get("internal_cpp_scheduling_suggestion")
            or (linked_idea or {}).get("internal_cpp_scheduling_suggestion")
            or ""
        ).strip()
        declared_recipe_value = (
            row.get("evaluation_recipe")
            or (linked_idea or {}).get("evaluation_recipe")
        )
        declared_recipe_id = str(declared_recipe_value or "").strip()
        if declared_recipe_id:
            if declared_recipe_id not in teacher_selectable_recipe_ids(template.evaluation_mode):
                errors.append(f"invalid_evaluation_recipe:{student_id}:{declared_recipe_id}")
                continue
            if not recipe_is_compatible_with_source_hooks(
                declared_recipe_id,
                hooks,
                evaluation_mode=template.evaluation_mode,
            ):
                errors.append(f"incompatible_evaluation_recipe:{student_id}:{declared_recipe_id}")
                continue
            executing_recipe_id = recipe_for_source_hooks(hooks, declared_recipe_id)
            if executing_recipe_id != declared_recipe_id:
                errors.append(f"unreachable_evaluation_recipe:{student_id}:{declared_recipe_id}")
                continue
        else:
            # Compatibility path for legacy callers. Codex Teacher plans are
            # structurally required to name their recipe before reaching this
            # controller, but direct policy hooks retain their historic
            # deterministic recipe inference for unit/ablation tools.
            executing_recipe_id = recipe_for_source_hooks(hooks, template.timing_recipe_id)
        execution_errors = _power_only_execution_admission_errors(
            hooks=hooks,
            source_evidence=evidence,
            evaluation_mode=template.evaluation_mode,
            recipe_id=executing_recipe_id,
            repository_graph=repository_graph,
        )
        if execution_errors:
            errors.extend(f"{student_id}:{error}" for error in execution_errors)
            continue
        if linked_idea is not None and declared_recipe_id:
            idea_recipe_id = str(linked_idea.get("evaluation_recipe") or "").strip()
            if idea_recipe_id and idea_recipe_id != declared_recipe_id:
                errors.append(f"explorer_evaluation_recipe_mismatch:{student_id}")
                continue
        referenced_cards = _items((linked_idea or {}).get("paper_card_ids"))
        unknown_cards = set(referenced_cards).difference(paper_card_ids)
        if unknown_cards:
            errors.append(f"unknown_paper_card:{student_id}:{sorted(unknown_cards)[0]}")
            continue
        if role == "explorer":
            if linked_idea is None:
                errors.append(f"unknown_explorer_idea:{student_id}:{idea_reference or 'none'}")
                continue
            idea_signals = _items(linked_idea.get("expected_signals"))
            idea_activation = _items(linked_idea.get("activation_signals")) or idea_signals
            if not set(idea_activation).issubset(idea_signals):
                errors.append(f"invalid_idea_activation_signals:{student_id}")
                continue
            if declared_activation and set(declared_activation) != set(idea_activation):
                errors.append(f"explorer_activation_signals_do_not_match_idea:{student_id}")
                continue
            activation_signals = idea_activation
            if not _same_or_compatible_idea(
                claim=claim,
                hooks=hooks,
                source_evidence=evidence,
                expected_signals=signals,
                idea=linked_idea,
            ):
                errors.append(f"explorer_assignment_does_not_match_idea:{student_id}")
                continue
            if _duplicate_explorer_idea(claim=claim, hooks=hooks, historical_ideas=normalized_history):
                errors.append(f"duplicate_explorer_idea:{student_id}")
                continue
            retrieval_errors = _explorer_retrieval_errors(
                idea=linked_idea,
                audit=explorer_retrieval_audit,
            )
            if retrieval_errors:
                errors.extend(f"{error}:{student_id}" for error in retrieval_errors)
                continue
            novelty_key = _novelty_key(claim, hooks)
            if novelty_key in used_explorer_keys:
                errors.append(f"duplicate_explorer_assignment:{student_id}")
                continue
            used_explorer_keys.add(novelty_key)
            role_records: tuple[str, ...] = ()
            predicted_effect = str(linked_idea.get("predicted_stage_effect") or "").strip()
            retrieval_ids = (f"teacher_idea:{idea_reference}",)
        else:
            activation_signals = declared_activation or signals
            role_records = _items(row.get("epd_record_ids"))
            if not _valid_epd_selection(template, role_records):
                errors.append(f"invalid_epd_references:{student_id}")
                continue
            predicted_effect = str(linked_idea.get("predicted_stage_effect") or "").strip() if linked_idea else ""
            retrieval_ids = (f"epd_{role}:{'|'.join(role_records)}",)
            novelty_key = f"{role}:{'|'.join(role_records)}:{_novelty_key(claim, hooks)}"
        result.append(
            Hypothesis(
                hypothesis_id=template.hypothesis_id,
                mechanism_family=_mechanism_family(claim, role),
                claim=claim,
                source_hooks=hooks,
                expected_signals=signals,
                activation_signals=activation_signals,
                retrieval_ids=retrieval_ids,
                novelty_key=novelty_key,
                scope_evidence=tuple(
                    f"teacher_source_evidence:{item}" for item in evidence
                ) + (
                    f"teacher_evaluation_recipe:{declared_recipe_id or executing_recipe_id}",
                    f"teacher_falsification:{falsification}",
                ),
                # Students may follow the bounded call chain inside campaign
                # roots. Exact file fences would again turn the Controller
                # into the mechanism author.
                allowed_patch_paths=(),
                evaluation_mode=template.evaluation_mode,
                timing_recipe_id=executing_recipe_id,
                student_role=template.student_role,
                role_mode=template.role_mode,
                epd_record_ids=role_records,
                teacher_idea_reference=idea_reference if role == "explorer" else "",
                student_id=student_id,
                teacher_diagnosis_summary=str(context.get("diagnosis_summary") or ""),
                teacher_parent_policy=str(context.get("parent_policy") or ""),
                teacher_selection_rationale=rationale,
                teacher_evolution_ideas=all_teacher_ideas,
                teacher_predicted_stage_effect=predicted_effect,
                teacher_internal_cpp_scheduling_suggestion=internal_cpp_scheduling_suggestion,
            )
        )
    return AssignmentMaterialization(tuple(result), tuple(dict.fromkeys(errors)))


def _items(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = list(value or ()) if isinstance(value, Iterable) else ()
    return tuple(dict.fromkeys(str(item).strip().strip("`") for item in values if str(item).strip()))


def _source_hook_items(value: object) -> tuple[str, ...]:
    values = (value,) if isinstance(value, str) else (
        list(value or ()) if isinstance(value, Iterable) else ()
    )
    return tuple(
        dict.fromkeys(
            item.strip().strip("`")
            for value in values
            for item in re.split(r"[;,]", str(value))
            if item.strip()
        )
    )


def _source_evidence_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return _items(value)
    anchors: list[str] = []
    legacy_next_anchor = re.compile(
        r"\s*,\s*(?=[^,;\s]+\.(?:cc|cpp|cxx|h|hh|hpp)::)"
    )
    for segment in value.split(";"):
        anchors.extend(legacy_next_anchor.split(segment))
    return tuple(
        dict.fromkeys(item.strip().strip("`") for item in anchors if item.strip())
    )


def _source_admission_errors(
    *,
    source_root: Path,
    hooks: Sequence[str],
    source_evidence: Sequence[str],
    allowed_patch_roots: Sequence[str],
    repository_graph: RepositoryGraph | None = None,
) -> tuple[str, ...]:
    allowed = tuple(root.strip("/") for root in allowed_patch_roots if root.strip("/"))
    errors: list[str] = []
    by_path: dict[str, list[str]] = {}
    for entry in source_evidence:
        path, separator, symbol = entry.partition("::")
        path, symbol = path.strip(), symbol.strip()
        if not separator or not path or not symbol:
            errors.append(f"invalid_source_evidence:{entry}")
            continue
        by_path.setdefault(path, []).append(symbol)
    for hook in hooks:
        relative = Path(hook)
        if relative.is_absolute() or ".." in relative.parts or relative.suffix not in _CPP_SUFFIXES:
            errors.append(f"invalid_source_hook:{hook}")
            continue
        if allowed and not any(hook == root or hook.startswith(root + "/") for root in allowed):
            errors.append(f"outside_allowed_patch_roots:{hook}")
            continue
        path = source_root / relative
        if not path.is_file():
            errors.append(f"missing_source_hook:{hook}")
            continue
        symbols = by_path.get(hook, [])
        if not symbols:
            errors.append(f"missing_source_evidence:{hook}")
            continue
        for symbol in symbols:
            evidence_anchor = f"{hook}::{symbol}"
            if repository_graph is not None:
                graph_file = repository_graph.files.get(hook)
                if graph_file is None:
                    errors.append(f"unverified_source_symbol:{evidence_anchor}")
                    continue
                if sha256_file(path) != graph_file.digest:
                    errors.append(f"stale_repository_graph:{hook}")
                    continue
                resolution = repository_graph.resolve_anchor(evidence_anchor)
                if resolution.status == "ambiguous":
                    errors.append(f"ambiguous_source_symbol:{evidence_anchor}")
                elif not resolution.resolved:
                    errors.append(f"unverified_source_symbol:{evidence_anchor}")
                continue
            try:
                contents = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                errors.append(f"unreadable_source_hook:{hook}")
                continue
            token = symbol.split("(", 1)[0].strip()
            if not token or token not in contents:
                errors.append(f"unverified_source_symbol:{evidence_anchor}")
    return tuple(errors)


def _valid_epd_selection(template: Hypothesis, selected: tuple[str, ...]) -> bool:
    options = {
        tuple(str(record_id) for record_id in list(option.get("epd_record_ids") or ()) if str(record_id))
        for option in template.candidate_options
    }
    if template.student_role == "integrator":
        return len(selected) == 2 and selected in options
    if template.student_role == "enhancer":
        return len(selected) == 1 and selected in options
    return not selected


def _power_only_execution_admission_errors(
    *,
    hooks: Sequence[str],
    source_evidence: Sequence[str],
    evaluation_mode: str,
    recipe_id: str,
    repository_graph: RepositoryGraph | None,
) -> tuple[str, ...]:
    """Require generic power hooks to be called by the dispatched source graph."""

    if evaluation_mode != "power_only":
        return ()
    direct_files = set(POWER_ONLY_EXECUTION_DIRECT_FILES)
    entry_symbols = dict(POWER_ONLY_EXECUTION_ENTRY_SYMBOLS)
    if recipe_id == "rmp_area_power":
        direct_files.update(RMP_AREA_EXECUTION_DIRECT_FILES)
        entry_symbols.update(RMP_AREA_EXECUTION_ENTRY_SYMBOLS)
    generic_hooks = tuple(hook for hook in hooks if hook not in direct_files)
    if not generic_hooks:
        return ()
    if repository_graph is None:
        return tuple(f"unreachable_power_source_hook:{hook}" for hook in generic_hooks)

    root_ids = {
        symbol.symbol_id
        for symbol in repository_graph.symbols.values()
        if symbol.qualified_name in entry_symbols.get(symbol.path, ())
    }
    reachable = _reachable_call_symbols(repository_graph, root_ids)
    evidence_by_path: dict[str, list[str]] = {}
    for anchor in source_evidence:
        path, separator, _ = anchor.partition("::")
        if separator:
            evidence_by_path.setdefault(path.strip(), []).append(anchor)

    errors: list[str] = []
    for hook in generic_hooks:
        resolutions = tuple(
            repository_graph.resolve_anchor(anchor)
            for anchor in evidence_by_path.get(hook, ())
        )
        if not resolutions or any(
            not resolution.resolved
            or not any(symbol.symbol_id in reachable for symbol in resolution.symbols)
            for resolution in resolutions
        ):
            errors.append(f"unreachable_power_source_hook:{hook}")
    return tuple(errors)


def _reachable_call_symbols(
    repository_graph: RepositoryGraph,
    root_ids: set[str],
) -> set[str]:
    targets: dict[str, list[str]] = {}
    for edge in repository_graph.edges:
        if edge.kind == "calls":
            targets.setdefault(edge.source, []).append(edge.target)
    reachable: set[str] = set()
    pending = sorted(root_ids, reverse=True)
    while pending:
        symbol_id = pending.pop()
        if symbol_id in reachable:
            continue
        reachable.add(symbol_id)
        pending.extend(
            target
            for target in sorted(targets.get(symbol_id, ()), reverse=True)
            if target not in reachable
        )
    return reachable


def _same_or_compatible_idea(
    *,
    claim: str,
    hooks: Sequence[str],
    source_evidence: Sequence[str],
    expected_signals: Sequence[str],
    idea: Mapping[str, object],
) -> bool:
    same_hooks = set(hooks) == set(_source_hook_items(idea.get("source_hooks")))
    if not same_hooks:
        return False
    same_grounding = (
        set(source_evidence) == set(_source_evidence_items(idea.get("source_evidence")))
        and set(expected_signals) == set(_items(idea.get("expected_signals")))
    )
    return same_grounding or _text_similarity(claim, str(idea.get("idea") or "")) >= 0.72


def _duplicate_explorer_idea(*, claim: str, hooks: Sequence[str], historical_ideas: Sequence[Mapping[str, object]]) -> bool:
    for previous in historical_ideas:
        previous_text = str(previous.get("idea") or previous.get("claim") or "")
        previous_hooks = _source_hook_items(previous.get("source_hooks"))
        if set(hooks) == set(previous_hooks) and _text_similarity(claim, previous_text) >= 0.72:
            return True
    return False


def _explorer_retrieval_errors(
    *,
    idea: Mapping[str, object],
    audit: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """Enforce Controller evidence for a fresh Explorer mechanism.

    ``None`` preserves the legacy direct-materialization API.  The Codex
    planning path always supplies an audit, where every Explorer idea needs a
    Controller-created draft-signature trace before it becomes executable.
    """
    if audit is None:
        return ()
    signature_id = str(idea.get("draft_signature_id") or "").strip()
    if not signature_id:
        return ("missing_explorer_draft_signature",)
    signatures = dict(audit.get("signatures") or {})
    row = dict(signatures.get(signature_id) or {})
    if not row or not bool(row.get("accepted")):
        return ("explorer_retrieval_audit_rejected",)
    required = (
        "epd_search_query",
        "retrieved_historical_ideas",
        "opened_epd_records",
        "nearest_historical_idea",
        "semantic_overlap",
        "material_difference",
        "novelty_conclusion",
    )
    if any(not str(idea.get(field) or "").strip() and not tuple(idea.get(field) or ()) for field in required):
        return ("missing_explorer_novelty_evidence",)
    query = str(idea.get("epd_search_query") or "").strip()
    if query != signature_id:
        return ("explorer_search_query_mismatch",)
    result_ids = {str(item) for item in list(row.get("result_ids") or ()) if str(item)}
    opened_ids = {str(item) for item in list(row.get("opened_idea_ids") or ()) if str(item)}
    cited_results = set(_items(idea.get("retrieved_historical_ideas")))
    cited_opened = set(_items(idea.get("opened_epd_records")))
    if not cited_results.issubset(result_ids) or not cited_opened.issubset(opened_ids):
        return ("explorer_novelty_evidence_not_in_trace",)
    nearest = str(idea.get("nearest_historical_idea") or "").strip()
    if result_ids and nearest not in result_ids:
        return ("explorer_nearest_idea_not_in_trace",)
    if not result_ids and nearest.lower() not in {"none", "no historical idea"}:
        return ("explorer_nearest_idea_not_in_trace",)
    return ()


def _text_similarity(left: str, right: str) -> float:
    left_words, right_words = set(_WORDS.findall(left.lower())), set(_WORDS.findall(right.lower()))
    if not left_words or not right_words:
        return 0.0
    return len(left_words.intersection(right_words)) / len(left_words.union(right_words))


def _novelty_key(claim: str, hooks: Sequence[str]) -> str:
    words = "_".join(sorted(set(_WORDS.findall(claim.lower()))))
    return f"{words}:{'|'.join(sorted(hooks))}"


def _mechanism_family(claim: str, role: str) -> str:
    tokens = _WORDS.findall(claim.lower())[:5]
    return f"teacher_{role}_{'_'.join(tokens) if tokens else 'mechanism'}"
