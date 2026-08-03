from __future__ import annotations

import json
from typing import Sequence

from ..core.contracts import GoalContract
from ..core.models import EvidenceVerdict, Hypothesis, Parent


def teacher_packet(*, contract: GoalContract, parent: Parent, round_index: int, retrieval_audit: dict[str, object], decision_context: dict[str, object] | None = None) -> str:
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
) -> str:
    rmp_controller_fact = (
        "For RMP timing recipes, the controller already passes the generated merged standard-cell Liberty and sets RMP_STA_SELECT_BEST_MODE=1. Do not remove, bypass, or broaden that gate in C++; it is already active for the assigned run. Preserve all existing guards and rollback behavior. For rmp_path_cone_timing and rmp_path_cone_halo_timing, the controller additionally owns union-of-four-paths-per-endpoint and the bounded one-level/16-instance upstream fanin halo; the halo recipe also sets RMP_PATH_CONE_ONLY=1. Edit only the source-side cone-quality/admission and fallback behavior assigned by the hypothesis, never those Tcl budgets."
        if hypothesis.timing_recipe_id in {"rmp_delay_timing", "rmp_path_cone_timing", "rmp_path_cone_halo_timing"}
        else ""
    )
    role_packet = _role_packet(hypothesis=hypothesis, epd_records=epd_records)
    teacher_handoff = _teacher_handoff(hypothesis)
    return "\n".join(
        [
            "# GoalEvolve v2 Student Packet",
            "",
            f"common_parent: {parent.parent_id}",
            f"parent_goal_distance: {parent.goal_distance:.8f}",
            "",
            "## Assigned Hypothesis",
            json.dumps(hypothesis.to_dict(), ensure_ascii=False, indent=2),
            "",
            teacher_handoff,
            "",
            "## Active Decision Stage",
            json.dumps(decision_context or {"mode": "single_stage"}, ensure_ascii=False, indent=2),
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
            *role_packet,
            "",
            "## Prior Negative Evidence",
            json.dumps(list(prior)[-8:], ensure_ascii=False, indent=2),
        ]
    )


def _role_packet(*, hypothesis: Hypothesis, epd_records: Sequence[dict[str, object]]) -> list[str]:
    records = [
        record
        for record in epd_records
        if str(record.get("record_id") or "") in set(hypothesis.epd_record_ids)
    ]
    if hypothesis.student_role == "integrator" and hypothesis.role_mode == "epd_integration":
        return [
            "## Integrator Operating Protocol",
            "You are integrating compatible, source-backed historical mechanisms whose measured attempts are not invalid. Read every referenced diff and its source-hook boundary before editing. Do not blindly apply, concatenate, or recreate historical patches. Keep only compatible decisions that fit the assigned source scope and current parent; resolve conflicts by preserving existing guards, journal rollback, Tcl ownership, and telemetry semantics. The historical records are evidence, not an inherited source tree or automatic promotion.",
            "## Selected EPD Evidence",
            json.dumps(records, ensure_ascii=False, indent=2),
        ]
    if hypothesis.student_role == "enhancer" and hypothesis.role_mode == "epd_enhancement":
        bundles = [
            {
                "record_id": record.get("record_id"),
                "idea_id": record.get("idea_id"),
                "previous_claim": dict(record.get("source_change_bundle") or {}).get("prior_claim"),
                "predicted_stage_effect": dict(record.get("source_change_bundle") or {}).get("prior_predicted_stage_effect"),
                "implementation_diff_artifact": record.get("implementation_diff_artifact"),
                "modified_files": dict(record.get("source_change_bundle") or {}).get("modified_files") or [],
                "added_code": dict(record.get("source_change_bundle") or {}).get("added_code") or [],
                "removed_code": dict(record.get("source_change_bundle") or {}).get("removed_code") or [],
                "mechanism_changes": dict(record.get("source_change_bundle") or {}).get("mechanism_changes") or [],
                "added_mechanism_changes": dict(record.get("source_change_bundle") or {}).get("added_mechanism_changes") or [],
                "removed_mechanism_changes": dict(record.get("source_change_bundle") or {}).get("removed_mechanism_changes") or [],
                "telemetry_changes": dict(record.get("source_change_bundle") or {}).get("telemetry_changes") or [],
            }
            for record in records
        ]
        return [
            "## Enhancer Operating Protocol",
            "You are strengthening one promising, source-backed historical mechanism. Inspect the prior source-change bundle and full diff before editing, identify the current bottleneck, and make one bounded refinement. Preserve the prior mechanism boundary; do not restart a suppressed experiment unchanged or broaden the patch into an unrelated rewrite. The historical result is a hypothesis seed, not a parent replacement.",
            "## Prior Source Change Bundle",
            json.dumps(bundles, ensure_ascii=False, indent=2),
            "## Selected EPD Evidence",
            json.dumps(records, ensure_ascii=False, indent=2),
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
        "You are an explorer. Form one fresh, bounded source-level idea from the Teacher claim, diagnosis, EDA/OpenROAD behavior, and assigned verified hook. Do not reuse a suppressed mechanism unchanged, import another Student's source change, or use EPD evidence as an unreviewed patch recipe.",
    ]


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
