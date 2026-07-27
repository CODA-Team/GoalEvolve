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


def student_packet(*, parent: Parent, hypothesis: Hypothesis, prior: Sequence[dict[str, object]], decision_context: dict[str, object] | None = None) -> str:
    rmp_controller_fact = (
        "For RMP timing recipes, the controller already passes the generated merged standard-cell Liberty and sets RMP_STA_SELECT_BEST_MODE=1. Do not remove, bypass, or broaden that gate in C++; it is already active for the assigned run. Preserve all existing guards and rollback behavior. For rmp_path_cone_timing and rmp_path_cone_halo_timing, the controller additionally owns union-of-four-paths-per-endpoint and the bounded one-level/16-instance upstream fanin halo; the halo recipe also sets RMP_PATH_CONE_ONLY=1. Edit only the source-side cone-quality/admission and fallback behavior assigned by the hypothesis, never those Tcl budgets."
        if hypothesis.timing_recipe_id in {"rmp_delay_timing", "rmp_path_cone_timing", "rmp_path_cone_halo_timing"}
        else ""
    )
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
            "## Active Decision Stage",
            json.dumps(decision_context or {"mode": "single_stage"}, ensure_ascii=False, indent=2),
            "",
            "## Mandatory Evidence",
            "Return a source diff, source commit, phase-signal values, frozen-contract metrics, and exactly these checks: build, flow, metrics, lec. A verified QoR gain can be promoted after 4/4 checks even when telemetry is missing; mark it unattributed and make repairing that telemetry a follow-up obligation.",
            "",
            "## Timing/Power Trade-off Discipline",
            "When the active stage is timing_recovery or adaptive_tradeoff, the controller—not you—selects the named repair_timing recipe and always executes/checkpoints repair_power before any timing phase. Do not edit Tcl or substitute a different recipe. In adaptive_tradeoff, obey the assigned source bucket: an upstream slot evolves repair_power, a downstream slot evolves repair_timing, and a handoff slot targets durable power moves or measured reversions; every slot still runs the complete power-then-timing flow and compares against its exact recipe baseline. Treat the assigned recipe as a controlled schedule experiment (LEGACY_MT/TNS/WNS/WNS_CONE/REROUTE/etc.); use its actual policy and command parameters when reasoning about the C++ change. Preserve or add structured source telemetry for eligible/considered moves, committed moves, journal rollbacks, retained moves, and a reason for any rejected timing-power trade-off. If your timing action reverses a power-reclaim cell replacement, explain and count that direction in the source telemetry; the evaluator independently compares checkpointed instance cell types and sends the overlap/reversion rate to the Teacher.",
            *([rmp_controller_fact] if rmp_controller_fact else []),
            "",
            "## Prior Negative Evidence",
            json.dumps(list(prior)[-8:], ensure_ascii=False, indent=2),
        ]
    )


def review_packet(*, verdicts: Sequence[EvidenceVerdict]) -> str:
    return json.dumps([verdict.to_dict() for verdict in verdicts], ensure_ascii=False, indent=2)
