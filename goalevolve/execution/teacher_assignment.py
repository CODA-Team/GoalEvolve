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
_EARLY_FORCED_RECLAIM_INACTIVE_SYMBOLS = frozenset({
    "rsz::RepairPowerPolicy::iterateLateLeakageRecovery",
    "rsz::RepairPowerPolicy::generateLateLeakageCandidates",
    "rsz::RepairPowerPolicy::tryCommitLateCandidate",
    "rsz::RepairPowerPolicy::tryCommitLateWindow",
    "rsz::RepairPowerPolicy::withinLateLeakageBudget",
    "rsz::RepairPowerPolicy::withinLateWeightedPowerTimingBudget",
})


@dataclass(frozen=True)
class AssignmentMaterialization:
    hypotheses: tuple[Hypothesis, ...]
    errors: tuple[str, ...]


def canonicalize_card_only_explorer_provenance(
    *,
    plan: Mapping[str, object],
    retrieval_audit: Mapping[str, object] | None,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Copy immutable retrieval provenance into card-only Explorer ideas.

    In ``openroad_cards`` mode the Teacher is allowed to inspect the live
    parent, but it must not become the authority for IDs emitted by the
    Controller's EPD retrieval trace.  A Controller repair frequently changes
    an assignment while preserving its draft signature.  Requiring the model
    to retype the trace IDs made a one-character transcription error abort an
    otherwise executable round.

    The caller deliberately invokes this only for card-only campaigns.  AST
    campaigns retain their existing verbatim graph/provenance path.  Semantic
    novelty prose remains Teacher-authored; only the trace-owned ID fields and
    query token are canonicalized from the accepted audit row.
    """
    normalized = dict(plan)
    signatures = (
        dict(retrieval_audit.get("signatures") or {})
        if isinstance(retrieval_audit, Mapping)
        else {}
    )
    explorer_references = {
        str(row.get("idea_reference") or "").strip()
        for row in list(plan.get("assignments") or ())
        if isinstance(row, Mapping)
        and str(row.get("role") or "").strip().lower() == "explorer"
        and str(row.get("idea_reference") or "").strip()
    }
    updates: list[dict[str, object]] = []
    records: list[object] = list(plan.get("evolution_idea_records") or ())
    rewritten_records: list[object] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            rewritten_records.append(raw)
            continue
        idea = dict(raw)
        reference = str(idea.get("reference") or "").strip()
        signature_id = str(idea.get("draft_signature_id") or "").strip()
        trace_row = dict(signatures.get(signature_id) or {})
        if (
            reference not in explorer_references
            or not signature_id
            or not bool(trace_row.get("accepted"))
        ):
            rewritten_records.append(idea)
            continue
        result_ids = _items(trace_row.get("result_ids"))
        opened_ids = _items(trace_row.get("opened_idea_ids"))
        nearest = str(idea.get("nearest_historical_idea") or "").strip()
        canonical_nearest = (
            nearest
            if nearest in set(result_ids)
            else (result_ids[0] if result_ids else "none")
        )
        canonical = {
            "epd_search_query": signature_id,
            "retrieved_historical_ideas": result_ids,
            "opened_epd_records": opened_ids,
            "nearest_historical_idea": canonical_nearest,
        }
        amended = {
            field: value
            for field, value in canonical.items()
            if (
                _items(idea.get(field))
                if field in {"retrieved_historical_ideas", "opened_epd_records"}
                else str(idea.get(field) or "").strip()
            )
            != value
        }
        idea.update(canonical)
        rewritten_records.append(idea)
        if amended:
            updates.append(
                {
                    "idea_reference": reference,
                    "draft_signature_id": signature_id,
                    "fields": sorted(amended),
                    "authority": "controller_retrieval_audit",
                }
            )
    if records:
        normalized["evolution_idea_records"] = rewritten_records
    return normalized, tuple(updates)


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
        scheduled_seed_option = _seed_revalidation_option(template)
        # A Controller-scheduled seed is not an open-ended Explorer choice.
        # Check its identity before any later recipe compatibility diagnostics
        # so a Teacher cannot obscure a substituted card behind an unrelated
        # malformed recipe.  The remaining card-shape checks stay below,
        # after the normal source/idea validation has established the input
        # is well formed.
        if scheduled_seed_option is not None:
            expected_seed_id = str(
                scheduled_seed_option.get("candidate_id") or ""
            ).strip()
            selected_seed_id = str(row.get("candidate_id") or "").strip()
            if selected_seed_id != expected_seed_id:
                errors.append(
                    f"seed_revalidation_candidate_mismatch:{student_id}:{expected_seed_id}"
                )
                continue
            missing_seed_hooks = tuple(
                hook
                for hook in _items(scheduled_seed_option.get("source_hooks"))
                if hook not in set(hooks)
            )
            if missing_seed_hooks:
                errors.extend(
                    f"seed_revalidation_missing_source_hook:{student_id}:"
                    f"{expected_seed_id}:{hook}"
                    for hook in missing_seed_hooks
                )
                continue
            if set(hooks) != set(
                _source_hook_items(scheduled_seed_option.get("source_hooks"))
            ):
                errors.append(
                    f"seed_revalidation_source_hooks_do_not_match_card:{student_id}:"
                    f"{expected_seed_id}"
                )
                continue
            missing_seed_anchors = tuple(
                anchor
                for anchor in _items(scheduled_seed_option.get("source_anchors"))
                if anchor not in set(evidence)
            )
            if missing_seed_anchors:
                errors.extend(
                    f"seed_revalidation_missing_source_anchor:{student_id}:"
                    f"{expected_seed_id}:{anchor}"
                    for anchor in missing_seed_anchors
                )
                continue
            if set(evidence) != set(
                _source_evidence_items(scheduled_seed_option.get("source_anchors"))
            ):
                errors.append(
                    f"seed_revalidation_source_anchors_do_not_match_card:{student_id}:"
                    f"{expected_seed_id}"
                )
                continue
            required_activation = (
                _items(scheduled_seed_option.get("activation_signals"))
                or _items(scheduled_seed_option.get("expected_signals"))
            )
            required_expected = _items(scheduled_seed_option.get("expected_signals"))
            missing_activation = tuple(
                signal
                for signal in required_activation
                if signal not in set(declared_activation)
            )
            if missing_activation:
                errors.extend(
                    f"seed_revalidation_missing_activation_signal:{student_id}:"
                    f"{expected_seed_id}:{signal}"
                    for signal in missing_activation
                )
                continue
            # Preserve the complete observation contract (including outcome
            # counters that may legitimately be zero) separately from the
            # smaller non-empty activation subset.
            if set(signals) != set(required_expected):
                errors.append(
                    f"seed_revalidation_expected_signals_do_not_match_card:{student_id}:"
                    f"{expected_seed_id}"
                )
                continue
            if set(declared_activation) != set(required_activation):
                errors.append(
                    f"seed_revalidation_activation_signals_do_not_match_card:{student_id}:"
                    f"{expected_seed_id}"
                )
                continue
        declared_recipe_value = (
            row.get("evaluation_recipe")
            or (linked_idea or {}).get("evaluation_recipe")
        )
        declared_recipe_id = str(declared_recipe_value or "").strip()
        # A scheduled seed has exactly one Controller-owned executable
        # recipe.  Its idea/assignment prose may still carry a stale or
        # illustrative recipe token, so do not let that token supersede the
        # envelope after the card identity was accepted above.
        if scheduled_seed_option is not None:
            seed_recipe_id = str(
                scheduled_seed_option.get("timing_recipe_id") or ""
            ).strip()
            declared_recipe_id = seed_recipe_id or template.timing_recipe_id
        if declared_recipe_id:
            if declared_recipe_id not in teacher_selectable_recipe_ids(template.evaluation_mode):
                errors.append(f"invalid_evaluation_recipe:{student_id}:{declared_recipe_id}")
                continue
            if not recipe_is_compatible_with_source_hooks(
                declared_recipe_id,
                hooks,
                evaluation_mode=template.evaluation_mode,
            ) and not _scheduled_seed_dispatch_is_reachable(
                seed_option=scheduled_seed_option,
                source_hooks=hooks,
                evaluation_mode=template.evaluation_mode,
                declared_recipe_id=declared_recipe_id,
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
            power_reclaim_phase=str(context.get("power_reclaim_phase") or ""),
        )
        if execution_errors:
            errors.extend(f"{student_id}:{error}" for error in execution_errors)
            continue
        if linked_idea is not None and declared_recipe_id and scheduled_seed_option is None:
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
            seed_option = scheduled_seed_option
            if seed_option is not None:
                # Card identity, hooks, anchors, and the split
                # expected/activation telemetry contract were checked before
                # recipe validation so their diagnostics remain authoritative.
                pass
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
            if seed_option is None:
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
            retrieval_ids = (
                f"historical_seed:{str(seed_option.get('candidate_id') or '').strip()}"
                if seed_option is not None
                else f"teacher_idea:{idea_reference}"
            ,)
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
                teacher_qor_causal_ledger=tuple(
                    dict(item)
                    for item in list(context.get("qor_causal_ledger") or ())
                    if isinstance(item, Mapping)
                ),
                teacher_internal_cpp_scheduling_suggestion=internal_cpp_scheduling_suggestion,
                candidate_options=template.candidate_options,
            )
        )
    return AssignmentMaterialization(tuple(result), tuple(dict.fromkeys(errors)))


def _seed_revalidation_option(template: Hypothesis) -> Mapping[str, object] | None:
    """Return the one controller-assigned historical seed for a fresh slot."""
    if template.student_role != "explorer" or template.role_mode != "seed_revalidation":
        return None
    options = tuple(
        option
        for option in template.candidate_options
        if str(option.get("candidate_id") or "").strip()
    )
    return options[0] if len(options) == 1 else None


def _scheduled_seed_dispatch_is_reachable(
    *,
    seed_option: Mapping[str, object] | None,
    source_hooks: Sequence[str],
    evaluation_mode: str,
    declared_recipe_id: str,
) -> bool:
    """Admit the one controller-scheduled PRP dispatch transform.

    The generic source graph correctly treats ``PowerRecoveryPlusPolicy`` as
    unreachable before this source transform exists.  R26's exact, parent
    hashed seed changes the adjacent Optimizer dispatch that makes it live;
    this narrow admission never applies to ordinary Teacher proposals.
    """
    if not isinstance(seed_option, Mapping):
        return False
    if str(seed_option.get("materialization_mode") or "").strip() != "exact_reference_patch":
        return False
    if str(seed_option.get("candidate_id") or "").strip() != "power_recovery_plus_late_profile":
        return False
    if evaluation_mode != "power_then_timing":
        return False
    if str(seed_option.get("timing_recipe_id") or "").strip() != "implicit_power_recovery_plus":
        return False
    if declared_recipe_id != "implicit_power_recovery_plus":
        return False
    return set(source_hooks) == {
        "src/rsz/src/Optimizer.cc",
        "src/rsz/src/policy/PowerRecoveryPlusPolicy.cc",
    }


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
            # Card-only mode intentionally has no AST symbol database.  A
            # live C++ definition may spell a member as ``Class::method``
            # inside a namespace rather than with its fully-qualified
            # ``ns::Class::method`` declarator, so accept only equivalent
            # suffix spellings while still requiring the declared source
            # path.  This is source presence validation, not reachability or
            # patch admission authority.
            declarator = symbol.split("(", 1)[0].strip()
            parts = tuple(part for part in declarator.split("::") if part)
            spellings = (
                declarator,
                "::".join(parts[-2:]),
                parts[-1] if parts else "",
            )
            if not any(spelling and spelling in contents for spelling in spellings):
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
    power_reclaim_phase: str = "",
) -> tuple[str, ...]:
    """Require generic power hooks to be called by the dispatched source graph."""

    if evaluation_mode != "power_only":
        return ()
    phase = power_reclaim_phase.strip()
    inactive_symbols = (
        _EARLY_FORCED_RECLAIM_INACTIVE_SYMBOLS
        if phase == "early_forced_reclaim"
        else frozenset()
    )
    phase_errors = tuple(
        f"inactive_power_phase_symbol:{phase}:{anchor}"
        for anchor in source_evidence
        if any(anchor.endswith(symbol) for symbol in inactive_symbols)
    )
    direct_files = set(POWER_ONLY_EXECUTION_DIRECT_FILES)
    entry_symbols = dict(POWER_ONLY_EXECUTION_ENTRY_SYMBOLS)
    if recipe_id == "rmp_area_power":
        direct_files.update(RMP_AREA_EXECUTION_DIRECT_FILES)
        entry_symbols.update(RMP_AREA_EXECUTION_ENTRY_SYMBOLS)
    # A card-only campaign deliberately has no AST call graph.  The
    # Controller's own execution contract nevertheless names the top-level
    # source entry files that the scheduled command invokes (for example
    # ``Resizer::repairPower`` and its Optimizer dispatch).  Those files are
    # not generic helpers and remain independently checked for a real live
    # anchor above.  Keep every other helper behind the AST reachability gate.
    direct_files.update(entry_symbols)
    # This admission is meaningful only when the Teacher actually grounded
    # its proposal in the repair_power/RMP dispatched subsystem.  Generic
    # unit and compatibility callers can use a power_only evaluation envelope
    # for a standalone timing hook; treating every such hook as a hidden
    # repair_power helper would reject source-valid assignments before their
    # own novelty/retrieval diagnostics are reached.
    has_declared_execution_entry = any(hook in direct_files for hook in hooks)
    has_indexed_execution_entry = repository_graph is not None and any(
        symbol.qualified_name in entry_symbols.get(symbol.path, ())
        for symbol in repository_graph.symbols.values()
    )
    if not has_declared_execution_entry and not has_indexed_execution_entry:
        return phase_errors
    generic_hooks = tuple(hook for hook in hooks if hook not in direct_files)
    if not generic_hooks:
        return phase_errors
    if repository_graph is None:
        return (*phase_errors, *(f"unreachable_power_source_hook:{hook}" for hook in generic_hooks))

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

    errors: list[str] = list(phase_errors)
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
