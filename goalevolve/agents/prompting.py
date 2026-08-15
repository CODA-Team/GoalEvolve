from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from ..core.io import load_json
from ..core.contracts import GoalContract
from ..core.models import EvidenceVerdict, Hypothesis, Parent
from ..planning.cross_design_experience import cross_design_experience_packet


def _assigned_hypothesis_view(hypothesis: Hypothesis) -> dict[str, object]:
    """Expose one Student's executable assignment, never its peer menu."""

    payload = hypothesis.to_dict()
    view = {
        key: payload[key]
        for key in (
            "hypothesis_id",
            "mechanism_family",
            "claim",
            "source_hooks",
            "expected_signals",
            "activation_signals",
            "scope_evidence",
            "allowed_patch_paths",
            "evaluation_mode",
            "timing_recipe_id",
            "student_role",
            "role_mode",
            "epd_record_ids",
            "epd_idea_id",
            "teacher_idea_reference",
            "student_id",
        )
        if key in payload
    }
    if hypothesis.role_mode == "seed_revalidation" and len(hypothesis.candidate_options) == 1:
        seed = dict(hypothesis.candidate_options[0])
        view["assigned_seed_revalidation"] = {
            key: seed[key]
            for key in (
                "candidate_id",
                "seed_id",
                "source_anchors",
                "decision_boundary",
                "summary",
                "reference_diff_paths",
            )
            if key in seed
        }
    return view


def teacher_packet(*, contract: GoalContract, parent: Parent, round_index: int, retrieval_audit: dict[str, object], decision_context: dict[str, object] | None = None) -> str:
    cross_design_experience = cross_design_experience_packet(
        decision_context=decision_context,
    )
    return "\n".join(
        [
            "# GoalEvolve v2 Teacher Packet",
            "",
            "You must diagnose from frozen target residuals and source-grounded evidence; do not use historical best scores or commercial-tool targets as a promotion oracle.",
            f"round: {round_index}",
            f"contract_id: {contract.contract_id}",
            f"common_parent: {parent.parent_id}",
            f"parent_goal_distance: {parent.goal_distance:.8f}",
            "",
            "## Frozen Contract",
            json.dumps(contract.to_dict(), ensure_ascii=False, indent=2),
            "",
            "## Retrieval Audit",
            json.dumps(retrieval_audit, ensure_ascii=False, indent=2),
            "",
            "## Active Decision Stage",
            json.dumps(decision_context or {"mode": "single_stage"}, ensure_ascii=False, indent=2),
            "",
            "## Cross-Design Iteration Experience",
            "These checked-in lessons are advisory process constraints. They never provide a patch, alter the frozen contract, or replace Controller promotion.",
            json.dumps(cross_design_experience, ensure_ascii=False, indent=2),
            "",
            "## Required Decision",
            "Assign independent mechanisms. Every assignment must name source hooks, predicted phase signals, a falsification condition, and protected metrics.",
        ]
    )


def student_packet(
    *,
    parent: Parent,
    hypothesis: Hypothesis,
    prior: Sequence[dict[str, object]],
    decision_context: dict[str, object] | None = None,
    epd_records: Sequence[dict[str, object]] = (),
    epd_root: Path | None = None,
    idea_record: Mapping[str, object] | None = None,
    integration_eligible_record_ids: Sequence[str] | None = None,
    enhancement_eligible_record_ids: Sequence[str] | None = None,
) -> str:
    rmp_controller_fact = (
        "For RMP timing recipes, the controller already passes the generated merged standard-cell Liberty and sets RMP_STA_SELECT_BEST_MODE=1. Do not remove, bypass, or broaden that gate in C++; it is already active for the assigned run. Preserve all existing guards and rollback behavior. For rmp_path_cone_timing and rmp_path_cone_halo_timing, the controller additionally owns union-of-four-paths-per-endpoint and the bounded one-level/16-instance upstream fanin halo; the halo recipe also sets RMP_PATH_CONE_ONLY=1. Edit only the source-side cone-quality/admission and fallback behavior assigned by the hypothesis, never those Tcl budgets."
        if hypothesis.timing_recipe_id in {"rmp_delay_timing", "rmp_path_cone_timing", "rmp_path_cone_halo_timing"}
        else ""
    )
    role_packet = _role_packet(
        hypothesis=hypothesis,
        epd_records=epd_records,
        epd_root=epd_root,
        idea_record=idea_record,
        integration_eligible_record_ids=integration_eligible_record_ids,
        enhancement_eligible_record_ids=enhancement_eligible_record_ids,
    )
    teacher_handoff = _teacher_handoff(hypothesis)
    assigned_hypothesis = _assigned_hypothesis_view(hypothesis)
    seed_reference = _seed_reference_packet(assigned_hypothesis)
    cross_design_experience = cross_design_experience_packet(
        decision_context=decision_context,
    )
    return "\n".join(
        [
            "# GoalEvolve v2 Student Packet",
            "",
            f"common_parent: {parent.parent_id}",
            f"parent_goal_distance: {parent.goal_distance:.8f}",
            "",
            "## Assigned Hypothesis",
            json.dumps(assigned_hypothesis, ensure_ascii=False, indent=2),
            "",
            *seed_reference,
            "",
            *role_packet,
            "",
            teacher_handoff,
            "",
            "## Active Decision Stage",
            json.dumps(decision_context or {"mode": "single_stage"}, ensure_ascii=False, indent=2),
            "",
            "## Cross-Design Iteration Experience",
            "These checked-in lessons are advisory constraints from completed official-flow campaigns. Apply the stage-relevant Student rule to the assigned hypothesis, then verify it against the live parent. They never authorize a patch outside the assignment, change the QoR contract, or bypass Controller promotion.",
            json.dumps(cross_design_experience, ensure_ascii=False, indent=2),
            "",
            "## Mandatory Evidence",
            "Return a source diff, source commit, phase-signal values, frozen-contract metrics, and exactly these checks: build, flow, metrics, lec. A verified QoR gain can be promoted after 4/4 checks even when telemetry is missing; mark it unattributed and make repairing that telemetry a follow-up obligation.",
            "",
            "## Internal C++ Scheduling Decision",
            "When the Teacher Handoff contains an Internal C++ Scheduling Suggestion, you may accept, adapt, or reject this advisory suggestion after independently inspecting the current source. In your final response, record exactly `- Decision: accepted|adapted|rejected` and `- Rationale: <concise source-grounded reason>`. You may change only internal C++ phase/policy scheduling within the assigned source boundary; do not edit Tcl, SDC, design, or benchmark inputs, and do not substitute a different Controller evaluation recipe.",
            "",
            "## Timing/Power Trade-off Discipline",
            "When the active stage is timing_recovery or adaptive_tradeoff, the controller—not you—selects the named repair_timing recipe and always executes/checkpoints repair_power before any timing phase. Do not edit Tcl or substitute a different recipe. In adaptive_tradeoff, the controller's candidate menu deliberately covers dominant-residual, repair_power-durability, and power-to-timing-reversion mechanisms; your role and selected hypothesis, not a hidden Student-number bucket, determine the source experiment. Every role still runs the complete power-then-timing flow and compares against its exact recipe baseline. Treat the assigned recipe as a controlled schedule experiment (LEGACY_MT/TNS/WNS/WNS_CONE/REROUTE/etc.); use its actual policy and command parameters when reasoning about the C++ change. Preserve or add structured source telemetry for eligible/considered moves, committed moves, journal rollbacks, retained moves, and a reason for any rejected timing-power trade-off. If your timing action reverses a power-reclaim cell replacement, explain and count that direction in the source telemetry; the evaluator independently compares checkpointed instance cell types and sends the overlap/reversion rate to the Teacher.",
            *([rmp_controller_fact] if rmp_controller_fact else []),
            "",
            "## Prior Negative Evidence",
            json.dumps(list(prior)[-8:], ensure_ascii=False, indent=2),
            "",
            "## Student Reflection",
            "After the Controller completes the build/flow/evidence evaluation, you will be resumed as this same Student identity for one observer-style reflection turn. Summarize in one evidence-grounded paragraph: intended mechanism, actual implementation, whether it activated, stage and final QoR effects, failure attribution, reusable lesson, an avoid-next-time mechanism, limitation, and bounded next reinforcement. From evidence, recommend exactly one of validated|promising|invalid|unactivated as the EPD lifecycle classification. The Controller alone decides promotion and never accepts a reflection as a substitute for official 4/4 evidence.",
        ]
    )


def _seed_reference_packet(assigned_hypothesis: Mapping[str, object]) -> list[str]:
    """Render only the assigned seed's source-pattern paths for a Student."""

    seed = assigned_hypothesis.get("assigned_seed_revalidation")
    if not isinstance(seed, Mapping):
        return []
    paths = [str(path).strip() for path in list(seed.get("reference_diff_paths") or ()) if str(path).strip()]
    if not paths:
        return []
    return [
        "## Seed Reference Discipline",
        "Before editing, read every listed historical implementation diff and port only the compatible bounded source mechanism after inspecting the live parent. These paths are source-pattern references only: do not infer or import historical QoR, parent identity, promotion, Tcl, or recipe behavior.",
        "The bounded revalidation diff must change the actual admitted, ordered, or committed cell set at the card's stated decision boundary. A trigger-only, environment-only, or telemetry-only change is not a seed revalidation: first identify the live candidate/admission/ordering/commit decision, then change that decision while preserving its existing timing, electrical, legality, and rollback guards.",
        json.dumps({"reference_diff_paths": paths}, ensure_ascii=False, indent=2),
    ]


def _role_packet(
    *,
    hypothesis: Hypothesis,
    epd_records: Sequence[dict[str, object]],
    epd_root: Path | None,
    idea_record: Mapping[str, object] | None,
    integration_eligible_record_ids: Sequence[str] | None,
    enhancement_eligible_record_ids: Sequence[str] | None,
) -> list[str]:
    records = [
        record
        for record in epd_records
        if str(record.get("record_id") or "") in set(hypothesis.epd_record_ids)
    ]
    root = Path(epd_root) if epd_root is not None else None
    cards = _mechanism_cards(root)
    mechanism_card_paths = {
        attempt_id: str(card.get("mechanism_card_path") or "")
        for card in cards
        for attempt_id in list(card.get("attempt_ids") or ())
        if attempt_id
    }
    directory = [
        _record_directory_row(
            record=record,
            epd_root=root,
            mechanism_card_path=mechanism_card_paths.get(str(record.get("record_id") or ""), ""),
        )
        for record in epd_records
    ]
    if hypothesis.student_role == "integrator" and hypothesis.role_mode == "epd_integration":
        if integration_eligible_record_ids is None:
            eligible_cards = [
                card for card in cards if str(card.get("status") or "") in {"validated", "promising"}
            ]
        else:
            eligible_cards = _mechanism_cards(
                root,
                eligible_record_ids=integration_eligible_record_ids,
            )
        selected_cards = [
            card for card in eligible_cards
            if set(str(item) for item in list(card.get("attempt_ids") or ())).intersection(
                hypothesis.epd_record_ids
            )
        ]
        return [
            "## Integrator Operating Protocol",
            "You are integrating source-backed mechanisms only after reading their mechanism cards, attempts, reflections, and diffs through the listed paths. Compare candidate ranking, commit/rollback guards, read/write sets, accumulated budgets, recipe assumptions, lineage, and source-diff overlap before editing. Do not concatenate historical patches; retain compatible boundaries and explicitly resolve a conflict or leave the pair uncombined.",
            "## Integrator Compatibility Directory",
            json.dumps(eligible_cards, ensure_ascii=False, indent=2),
            "## Selected Integration Dossier",
            json.dumps({"mechanism_cards": selected_cards, "attempts": [_record_directory_row(record=record, epd_root=root, mechanism_card_path=mechanism_card_paths.get(str(record.get("record_id") or ""), "")) for record in records]}, ensure_ascii=False, indent=2),
        ]
    if hypothesis.student_role == "enhancer" and hypothesis.role_mode == "epd_enhancement":
        enhancer_directory = [
            row
            for row in directory
            if row.get("epd_status") == "promising"
            or bool(row.get("inherited_by_current_parent"))
        ]
        if enhancement_eligible_record_ids is not None:
            eligible_ids = {str(record_id) for record_id in enhancement_eligible_record_ids}
            enhancer_directory = [
                row for row in enhancer_directory if str(row.get("record_id") or "") in eligible_ids
            ]
        return [
            "## Enhancer Operating Protocol",
            "You are strengthening one source-backed mechanism with unresolved evidence or one validated mechanism already inherited by the current parent. First read its complete idea, attempt, Student reflection, metrics, and diff through the dossier paths; identify whether candidate quality, debt, downstream reversal, commit/rollback guard, objective mismatch, or interaction caused the missed retention. For an inherited mechanism, the parent already contains its prior patch: name the existing decision that changes the actual candidate/cell set, then make one bounded refinement to that admission, ordering, or commit decision. Do not spend the turn on trigger-only, environment-only, or telemetry-only changes when the selected cell set would remain unchanged. Do not restart a suppressed patch or make an unrelated rewrite.",
            "## Enhancer Candidate Directory",
            json.dumps(enhancer_directory, ensure_ascii=False, indent=2),
            "## Previous-round Enhancer Dossier",
            json.dumps([_record_directory_row(record=record, epd_root=root, include_summary=True, mechanism_card_path=mechanism_card_paths.get(str(record.get("record_id") or ""), "")) for record in records], ensure_ascii=False, indent=2),
        ]
    if hypothesis.student_role == "integrator":
        return [
            "## Integrator Bootstrap Protocol",
            "No validated historical pair is available yet. Establish one clearly factored, telemetry-complete mechanism that can later be combined with independently validated evidence. Do not claim a crossover or reuse an unvalidated peer patch.",
        ]
    if hypothesis.student_role == "enhancer":
        return [
            "## Enhancer Bootstrap Protocol",
            "No validated historical mechanism is available yet. Create one bounded refinement seed with explicit activation and falsification evidence; it becomes eligible for enhancement only after controller validation.",
        ]
    return [
        "## Explorer Operating Protocol",
        "You are an explorer. Form one fresh, bounded source-level mechanism from the Teacher claim, diagnosis, EDA/OpenROAD behavior, and assigned verified hook. Do not reuse a suppressed mechanism unchanged, import another Student's source change, or treat EPD evidence as an unreviewed patch recipe.",
        "## Explorer Novelty Obligations",
        json.dumps(_explorer_novelty(idea_record=idea_record, epd_root=root), ensure_ascii=False, indent=2),
        "The Teacher/Controller completed the history audit before this assignment. Preserve the audited decision boundary; if live source disproves it, report that discrepancy rather than silently replacing it with a historical mechanism.",
    ]


def _record_directory_row(
    *,
    record: Mapping[str, object],
    epd_root: Path | None,
    include_summary: bool = False,
    mechanism_card_path: str = "",
) -> dict[str, object]:
    record_id = str(record.get("record_id") or "")
    idea_id = str(record.get("idea_id") or "")
    attempt_root = epd_root / "attempts" / record_id if epd_root is not None and record_id else None
    row = {
        "record_id": record_id,
        "idea_id": idea_id,
        "epd_status": record.get("epd_status"),
        "inherited_by_current_parent": bool(record.get("inherited_by_current_parent")),
        "source_hooks": list(record.get("source_hooks") or ()),
        "idea_path": str(epd_root / "ideas" / idea_id / "idea.json") if epd_root is not None and idea_id else "",
        "mechanism_card_path": mechanism_card_path,
        "attempt_path": str(attempt_root / "attempt.json") if attempt_root is not None else "",
        "stage_metrics_path": str(attempt_root / "stage_metrics.json") if attempt_root is not None else "",
        "phase_signals_path": str(attempt_root / "phase_signals.json") if attempt_root is not None else "",
        "student_reflection_path": str(attempt_root / "student_reflection.md") if attempt_root is not None else "",
        "implementation_diff_artifact": str(attempt_root / "implementation.diff") if attempt_root is not None else str(record.get("implementation_diff_artifact") or ""),
        "evidence_manifest_path": str(attempt_root / "evidence_manifest.json") if attempt_root is not None else "",
    }
    if include_summary:
        signals = dict(record.get("phase_signals") or {})
        expected = tuple(str(name) for name in list(record.get("expected_signals") or ()) if name)
        reported = {str(name) for name in signals}
        nonzero = sum(
            1
            for value in signals.values()
            if isinstance(value, (int, float)) and float(value) != 0.0
        )
        row.update(
            {
                "evidence_state": record.get("evidence_state") or "read attempt_path",
                "goal_distance": record.get("goal_distance"),
                "distance_gain": record.get("distance_gain"),
                "activation_summary": {
                    "expected_signal_count": len(expected),
                    "reported_signal_count": len(reported),
                    "nonzero_signal_count": nonzero,
                    "all_expected_signals_reported": bool(expected) and set(expected).issubset(reported),
                },
                "mechanism_summary": dict(record.get("source_change_bundle") or {}).get("prior_claim") or "read idea_path",
                "predicted_stage_effect": dict(record.get("source_change_bundle") or {}).get("prior_predicted_stage_effect") or "read attempt_path",
            }
        )
    return row


def _mechanism_cards(
    epd_root: Path | None,
    *,
    eligible_record_ids: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    if epd_root is None:
        return []
    manifest = load_json(epd_root / "manifest.json", {}) or {}
    cards: list[dict[str, object]] = []
    eligible_ids = (
        {str(record_id) for record_id in eligible_record_ids}
        if eligible_record_ids is not None
        else None
    )
    for row in list(dict(manifest).get("mechanisms") or ()):
        if not isinstance(row, Mapping):
            continue
        path = Path(str(row.get("path") or ""))
        card = load_json(path, {}) or {}
        if not isinstance(card, Mapping):
            continue
        attempt_ids = [str(item) for item in list(card.get("attempt_ids") or ()) if item]
        selected_attempt_ids = (
            [attempt_id for attempt_id in attempt_ids if attempt_id in eligible_ids]
            if eligible_ids is not None
            else attempt_ids
        )
        if eligible_ids is not None and not selected_attempt_ids:
            continue
        selected_ids = set(selected_attempt_ids)
        observed_qor = [
            dict(effect)
            for effect in list(card.get("observed_qor_effects") or ())
            if isinstance(effect, Mapping)
            and (eligible_ids is None or str(effect.get("record_id") or "") in selected_ids)
        ]
        downstream = [
            dict(effect)
            for effect in list(card.get("downstream_retention") or ())
            if isinstance(effect, Mapping)
            and (eligible_ids is None or str(effect.get("record_id") or "") in selected_ids)
        ]
        gains = [
            float(effect["distance_gain"])
            for effect in observed_qor
            if isinstance(effect.get("distance_gain"), (int, float))
        ]
        rendered = {
            key: card.get(key)
            for key in (
                "mechanism_id",
                "status",
                "mechanism_summary",
                "decision_boundary",
                "source_hooks",
                "state_read_set",
                "source_write_set",
                "action_type",
                "commit_scope",
                "dependencies",
                "known_conflicts",
                "parent_compatibility",
            )
        }
        rendered.update(
            {
                "attempt_ids": selected_attempt_ids,
                "qor_summary": {
                    "attempt_count": len(observed_qor),
                    "positive_distance_gain_count": sum(gain > 0.0 for gain in gains),
                    "best_distance_gain": max(gains) if gains else None,
                },
                "downstream_retention_summary": {
                    "attempt_count": len(downstream),
                    "has_checkpoint_evidence": bool(downstream),
                },
                "student_reflection_paths": (
                    [str(epd_root / "attempts" / attempt_id / "student_reflection.md") for attempt_id in selected_attempt_ids]
                    if eligible_ids is not None
                    else list(card.get("student_reflection_paths") or ())
                ),
                "implementation_artifact_paths": (
                    [str(epd_root / "attempts" / attempt_id / "implementation.diff") for attempt_id in selected_attempt_ids]
                    if eligible_ids is not None
                    else list(card.get("implementation_artifact_paths") or ())
                ),
            }
        )
        rendered["mechanism_card_path"] = str(path)
        cards.append(rendered)
    return cards


def _explorer_novelty(
    *, idea_record: Mapping[str, object] | None,
    epd_root: Path | None,
) -> dict[str, object]:
    idea = dict(idea_record or {})
    return {
        "draft_signature_id": idea.get("draft_signature_id") or "not recorded",
        "epd_search_query": idea.get("epd_search_query") or "not recorded",
        "retrieved_historical_ideas": list(idea.get("retrieved_historical_ideas") or ()),
        "opened_epd_records": list(idea.get("opened_epd_records") or ()),
        "nearest_historical_idea": idea.get("nearest_historical_idea") or "none",
        "semantic_overlap": idea.get("semantic_overlap") or "not recorded",
        "material_difference": idea.get("material_difference") or "not recorded",
        "novelty_conclusion": idea.get("novelty_conclusion") or "not recorded",
        "epd_root": str(epd_root) if epd_root is not None else "not supplied",
    }


def _teacher_handoff(hypothesis: Hypothesis) -> str:
    lines = ["## Teacher Handoff"]
    if hypothesis.teacher_diagnosis_summary:
        lines.extend(["### Diagnosis", hypothesis.teacher_diagnosis_summary])
    if hypothesis.teacher_parent_policy:
        lines.extend(["### Parent Policy", hypothesis.teacher_parent_policy])
    if hypothesis.teacher_evolution_ideas:
        lines.extend(
            ["### Evolution Ideas", *(f"- {idea}" for idea in hypothesis.teacher_evolution_ideas)]
        )
    if hypothesis.teacher_predicted_stage_effect:
        lines.extend(["### Predicted Stage Effect", hypothesis.teacher_predicted_stage_effect])
    if hypothesis.teacher_selection_rationale:
        lines.extend(["### Why This Assignment", hypothesis.teacher_selection_rationale])
    if hypothesis.teacher_internal_cpp_scheduling_suggestion:
        lines.extend(
            [
                "### Internal C++ Scheduling Suggestion",
                hypothesis.teacher_internal_cpp_scheduling_suggestion,
                "This is advisory. Independently accept, adapt, or reject it after inspecting the current source; do not change the external Tcl recipe or any benchmark input.",
            ]
        )
    if len(lines) == 1:
        lines.append("Follow the controller-created role and source boundary for this round.")
    return "\n".join(lines)


def review_packet(*, verdicts: Sequence[EvidenceVerdict]) -> str:
    return json.dumps([verdict.to_dict() for verdict in verdicts], ensure_ascii=False, indent=2)
