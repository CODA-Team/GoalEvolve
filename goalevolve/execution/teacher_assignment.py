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
from ..planning.timing_recovery import (
    recipe_for_source_hooks,
    recipe_is_compatible_with_source_hooks,
    recipes_for_students,
    teacher_selectable_recipe_ids,
)


_CPP_SUFFIXES = frozenset({".cc", ".cpp", ".cxx", ".hh", ".hpp"})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WORDS = re.compile(r"[a-z0-9_]+")


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
) -> dict[str, dict[str, object]]:
    """Return a compact live-source index for the Teacher, not an AST gate."""
    roots = [source_root / root.strip("/") for root in allowed_patch_roots if root.strip("/")]
    roots = [root for root in roots if root.is_dir()] or [source_root / "src"]
    files: dict[str, dict[str, object]] = {}
    function = re.compile(r"(?:^|\n)\s*[\w:<>~,&*\s]+\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?\{")
    metric = re.compile(r"METRIC\|([A-Za-z0-9_]+)")
    ignored = {"test", "tests", "third-party", "build", "__pycache__"}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            relative_parts = path.relative_to(source_root).parts if path.exists() else ()
            if not path.is_file() or path.suffix not in _CPP_SUFFIXES or ignored.intersection(relative_parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
                relative = str(path.relative_to(source_root))
            except (OSError, ValueError):
                continue
            files[relative] = {
                "symbols": sorted(set(function.findall(text)))[:max_symbols_per_file],
                "existing_metric_signals": sorted(set(metric.findall(text)))[:max_symbols_per_file],
            }
    # The prompt needs a map, not an embedded second source tree. Retain a
    # compact, deterministic sample from each permitted root and direct Codex
    # to inspect the authoritative snapshot with rg for all final anchors.
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
        hooks = _items(row.get("source_hooks"))
        signals = _items(row.get("expected_signals"))
        evidence = _items(row.get("source_evidence"))
        falsification = str(row.get("falsification_condition") or "").strip()
        if not claim or not rationale or not hooks or not signals or not evidence or not falsification:
            errors.append(f"incomplete_assignment:{student_id}")
            continue
        source_errors = _source_admission_errors(
            source_root=source_root,
            hooks=hooks,
            source_evidence=evidence,
            allowed_patch_roots=allowed_patch_roots,
        )
        if source_errors:
            errors.extend(f"{student_id}:{error}" for error in source_errors)
            continue
        invalid_signals = [signal for signal in signals if not _IDENTIFIER.fullmatch(signal)]
        if invalid_signals:
            errors.append(f"invalid_expected_signal:{student_id}:{invalid_signals[0]}")
            continue
        idea_reference = str(row.get("idea_reference") or "").strip()
        linked_idea = ideas.get(idea_reference)
        declared_recipe_value = (
            row.get("evaluation_recipe")
            or (linked_idea or {}).get("evaluation_recipe")
        )
        declared_recipe_id = str(declared_recipe_value or "").strip()
        if declared_recipe_id:
            if declared_recipe_id not in teacher_selectable_recipe_ids(template.evaluation_mode):
                errors.append(f"invalid_evaluation_recipe:{student_id}:{declared_recipe_id}")
                continue
            if not recipe_is_compatible_with_source_hooks(declared_recipe_id, hooks):
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
            novelty_key = _novelty_key(claim, hooks)
            if novelty_key in used_explorer_keys:
                errors.append(f"duplicate_explorer_assignment:{student_id}")
                continue
            used_explorer_keys.add(novelty_key)
            role_records: tuple[str, ...] = ()
            predicted_effect = str(linked_idea.get("predicted_stage_effect") or "").strip()
            retrieval_ids = (f"teacher_idea:{idea_reference}",)
        else:
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
            )
        )
    return AssignmentMaterialization(tuple(result), tuple(dict.fromkeys(errors)))


def _items(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = list(value or ()) if isinstance(value, Iterable) else ()
    return tuple(dict.fromkeys(str(item).strip().strip("`") for item in values if str(item).strip()))


def _source_admission_errors(
    *,
    source_root: Path,
    hooks: Sequence[str],
    source_evidence: Sequence[str],
    allowed_patch_roots: Sequence[str],
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
        try:
            contents = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            errors.append(f"unreadable_source_hook:{hook}")
            continue
        for symbol in symbols:
            token = symbol.split("(", 1)[0].strip()
            if not token or token not in contents:
                errors.append(f"unverified_source_symbol:{hook}::{symbol}")
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


def _same_or_compatible_idea(
    *,
    claim: str,
    hooks: Sequence[str],
    source_evidence: Sequence[str],
    expected_signals: Sequence[str],
    idea: Mapping[str, object],
) -> bool:
    same_hooks = set(hooks) == set(_items(idea.get("source_hooks")))
    if not same_hooks:
        return False
    same_grounding = (
        set(source_evidence) == set(_items(idea.get("source_evidence")))
        and set(expected_signals) == set(_items(idea.get("expected_signals")))
    )
    return same_grounding or _text_similarity(claim, str(idea.get("idea") or "")) >= 0.72


def _duplicate_explorer_idea(*, claim: str, hooks: Sequence[str], historical_ideas: Sequence[Mapping[str, object]]) -> bool:
    for previous in historical_ideas:
        previous_text = str(previous.get("idea") or previous.get("claim") or "")
        previous_hooks = _items(previous.get("source_hooks"))
        if set(hooks) == set(previous_hooks) and _text_similarity(claim, previous_text) >= 0.72:
            return True
    return False


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
