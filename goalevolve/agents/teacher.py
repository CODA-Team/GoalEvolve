from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .codex_runtime import CodexRuntimeConfig, PersistentCodexRunner
from ..planning.diagnosis import Diagnosis
from ..planning.epd import EvolutionProgramDatabase
from ..core.models import CandidateResult, EvidenceVerdict, Hypothesis, Parent
from ..planning.observations import ObservationMemory
from ..planning.timing_recovery import schedule_memory_summary


@dataclass(frozen=True)
class CodexTeacherConfig:
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "xhigh"
    retries: int = 3
    timeout_s: int = 3600
    seed_home: Path = Path("outputs/codex_home")
    credential_env: Path | None = None


@dataclass(frozen=True)
class TeacherPlan:
    hypotheses: tuple[Hypothesis, ...]
    diagnosis: Diagnosis
    plan: dict[str, object]
    artifacts: dict[str, str]


class HeuristicTeacher:
    """Deterministic Teacher used only for controller smoke tests and ablations."""

    name = "heuristic_teacher"

    def plan(self, *, diagnosis: Diagnosis, fallback: Sequence[Hypothesis], **_: object) -> TeacherPlan:
        return TeacherPlan(tuple(fallback), diagnosis, {"planner_mode": "heuristic", "diagnosis": diagnosis.to_dict(), "hypotheses": [item.to_dict() for item in fallback]}, {})

    def review(self, *, diagnosis: Diagnosis, rows: Sequence[tuple[CandidateResult, EvidenceVerdict]], **_: object) -> dict[str, object]:
        return {"planner_mode": "heuristic", "diagnosis": diagnosis.to_dict(), "outcomes": [_outcome(candidate, verdict) for candidate, verdict in rows]}


class CodexTeacher:
    """Persistent Teacher that diagnoses EPD/checkpoint evidence and plans Students."""

    name = "codex_teacher"

    def __init__(self, config: CodexTeacherConfig) -> None:
        self.runner = PersistentCodexRunner(CodexRuntimeConfig(model=config.model, reasoning_effort=config.reasoning_effort, retries=config.retries, timeout_s=config.timeout_s, seed_home=config.seed_home, credential_env=config.credential_env))

    def plan(
        self,
        *,
        state_root: Path,
        round_root: Path,
        round_index: int,
        parent: Parent,
        contract=None,
        diagnosis: Diagnosis,
        fallback: Sequence[Hypothesis],
        previous_review: dict[str, object],
        decision_context: dict[str, object] | None = None,
    ) -> TeacherPlan:
        epd = EvolutionProgramDatabase(state_root).teacher_summary()
        observations = ObservationMemory(state_root).summary()
        schedule_memory = schedule_memory_summary(state_root)
        prompt = self._plan_prompt(contract=contract, parent=parent, diagnosis=diagnosis, epd=epd, observations=observations, schedule_memory=schedule_memory, previous_review=previous_review, fallback=fallback, decision_context=decision_context)
        # Planning and review for one round share compact local context; the
        # next round starts a clean thread because EPD/review are supplied in
        # its packet.  This bounds remote conversation-token accumulation.
        turn = self.runner.run(state_root=state_root, identity=self._round_identity(round_index), operation_id=f"r{round_index:03d}_teacher_plan", cwd=round_root, artifact_root=round_root / "teacher" / "plan", prompt=prompt)
        raw = self._read_json(turn.artifacts.get("codex_last_message")) if turn.ok else {}
        hypotheses = self._sanitize_hypotheses(raw.get("hypotheses"), fallback)
        plan = {"schema_version": "goalevolve.v2.teacher_plan.v1", "teacher_ok": turn.ok, "teacher_detail": turn.detail, "diagnosis": diagnosis.to_dict(), "epd": epd, "observations": observations, "timing_schedule_memory": schedule_memory, "previous_review": previous_review, "raw_plan": raw, "hypotheses": [item.to_dict() for item in hypotheses]}
        return TeacherPlan(tuple(hypotheses), diagnosis, plan, turn.artifacts)

    def review(
        self,
        *,
        state_root: Path,
        round_root: Path,
        round_index: int,
        parent: Parent,
        parent_after: Parent | None = None,
        promoted_student: str | None = None,
        decision_context: dict[str, object] | None = None,
        diagnosis: Diagnosis,
        rows: Sequence[tuple[CandidateResult, EvidenceVerdict]],
    ) -> dict[str, object]:
        epd = EvolutionProgramDatabase(state_root).teacher_summary()
        observations = ObservationMemory(state_root).summary()
        schedule_memory = schedule_memory_summary(state_root)
        prompt = self._review_prompt(
            parent=parent,
            parent_after=parent_after or parent,
            promoted_student=promoted_student,
            decision_context=decision_context,
            diagnosis=diagnosis,
            epd=epd,
            observations=observations,
            schedule_memory=schedule_memory,
            rows=rows,
        )
        turn = self.runner.run(state_root=state_root, identity=self._round_identity(round_index), operation_id=f"r{round_index:03d}_teacher_review", cwd=round_root, artifact_root=round_root / "teacher" / "review", prompt=prompt)
        raw = self._read_json(turn.artifacts.get("codex_last_message")) if turn.ok else {}
        return {
            "schema_version": "goalevolve.v2.teacher_review.v1",
            "teacher_ok": turn.ok,
            "teacher_detail": turn.detail,
            "diagnosis": diagnosis.to_dict(),
            "decision_context": decision_context or {},
            "controller_decision": {
                "promoted_student": promoted_student,
                "parent_after": (parent_after or parent).to_dict(),
                "authority": "deterministic_promotion_controller",
            },
            "epd": epd,
            "observations": observations,
            "timing_schedule_memory": schedule_memory,
            "outcomes": [_outcome(candidate, verdict) for candidate, verdict in rows],
            "raw_review": raw,
            "artifacts": turn.artifacts,
        }

    @staticmethod
    def _read_json(path_value: str | None) -> dict[str, object]:
        if not path_value:
            return {}
        try:
            text = Path(path_value).read_text(encoding="utf-8", errors="ignore")
            start, end = text.find("{"), text.rfind("}")
            payload = json.loads(text[start : end + 1]) if start >= 0 and end > start else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _round_identity(round_index: int) -> str:
        """Keep plan/review continuity within, but never across, a round."""
        return f"teacher_r{round_index:03d}"

    @staticmethod
    def _sanitize_hypotheses(raw: object, fallback: Sequence[Hypothesis]) -> list[Hypothesis]:
        rows = [item for item in list(raw or []) if isinstance(item, dict)]
        selected: list[Hypothesis] = []
        for base in fallback:
            row = next((item for item in rows if str(item.get("student_id") or "") in base.hypothesis_id), {})
            allowed_hooks = tuple(hook for hook in list(row.get("source_hooks") or []) if hook in base.source_hooks) or base.source_hooks
            expected = tuple(signal for signal in list(row.get("expected_signals") or []) if signal in base.expected_signals) or base.expected_signals
            claim = str(row.get("claim") or base.claim).strip()
            # Retrieval and the stage controller own experiment allocation.
            # Once they emit a source-verified slot, the Teacher may make its
            # decision rule more precise but may not silently turn the slot
            # into advice to do nothing.  Such prose previously reached the
            # Student as an executable packet: the Student correctly left the
            # mechanism disabled, a full official flow was wasted, and the
            # telemetry repair could only describe that non-execution.  Keep
            # one hypothesis per supplied slot and restore the authoritative
            # card claim whenever the Teacher attempts suppression/no-op.
            normalized_claim = " ".join(claim.lower().replace("-", " ").split())
            no_op_markers = (
                "withhold execution",
                "withholding execution",
                "do not edit",
                "do not implement",
                "do not execute",
                "do not run",
                "remain suppressed",
                "remains suppressed",
                "retain the parent",
                "not an admissible fresh activation",
                "only if an authoritative controller revision",
            )
            if any(marker in normalized_claim for marker in no_op_markers):
                claim = base.claim
            # The Teacher is allowed to refine the decision rule within a
            # verified slot, but it must not cross-wire independently tested
            # source mechanisms.  In particular, a model can return the
            # correct ``student_id`` while accidentally describing a policy
            # from a neighbouring slot.  Hooks/recipes are controller-owned;
            # discard that contradictory prose rather than passing a confused
            # packet to the editing Student.
            other_markers = {
                marker
                for other in fallback
                if other.hypothesis_id != base.hypothesis_id
                for hook in other.source_hooks
                for marker in (Path(hook).stem,)
            }
            own_markers = {Path(hook).stem for hook in base.source_hooks}
            if any(marker in claim for marker in other_markers - own_markers):
                claim = base.claim
            selected.append(Hypothesis(base.hypothesis_id, base.mechanism_family, claim, allowed_hooks, expected, base.retrieval_ids, base.novelty_key, base.scope_evidence + ((f"teacher_falsification:{str(row.get('falsification_condition') or '').strip()}",) if row.get("falsification_condition") else ()), base.allowed_patch_paths, base.evaluation_mode, base.timing_recipe_id, base.activation_signals, base.conclusive_nonactivation_patterns))
        return selected

    @staticmethod
    def _plan_prompt(*, parent: Parent, diagnosis: Diagnosis, epd: dict[str, object], observations: dict[str, object], schedule_memory: dict[str, object] | None = None, previous_review: dict[str, object], fallback: Sequence[Hypothesis], contract=None, decision_context: dict[str, object] | None = None) -> str:
        contract_view = contract.to_dict() if contract is not None and hasattr(contract, "to_dict") else {}
        # Put the decision semantics in the structured stage payload as well
        # as in controller code.  This prevents the model from interpreting
        # signed checkpoint movements backwards or adding a full-contract
        # distance gate to the deliberately staged power search.
        decision_context = dict(decision_context or {"mode": "single_stage"})
        decision_context["checkpoint_effect_semantics"] = {
            "formula_for_minimized_metrics": "before - after",
            "positive": "stage improved the metric",
            "negative": "stage regressed the metric",
            "zero": "no measured change",
            "unresolved_debt": "nonnegative loss from the best checkpoint to the final checkpoint",
        }
        if decision_context.get("stage") == "power_reclaim":
            decision_context["full_contract_distance_required_for_promotion"] = False
            decision_context["teacher_falsification_rule"] = (
                "Use only strict lexicographic leakage/dynamic residual improvement, "
                "complete official integrity evidence, zero DRV, and the explicit TNS "
                "safety ceiling. Do not additionally require full three-metric distance, "
                "TNS, or raw dynamic power to improve over the parent."
            )
        elif decision_context.get("stage") == "adaptive_tradeoff":
            decision_context["teacher_falsification_rule"] = (
                "Require the dominant normalized residual and full frozen-contract distance to both improve, "
                "preserve all already-satisfied targets, and use the exact recipe no-diff baseline. Keep one "
                "upstream repair_power slot and one power-to-timing handoff slot even when TNS is dominant."
            )
        return "\n".join(["# GoalEvolve Persistent Teacher: diagnose and plan", "", "You are the Teacher. Diagnose the frozen-goal gap using only the supplied compact EPD, parent checkpoint trajectory, source-grounded candidate hooks, and empirical observation memory. You do not edit source code.", "The frozen QoR decision contract is exactly TNS, dynamic power, and leakage power. Runtime is execution telemetry only: never use it to choose, rank, retain, suppress, or promote a mechanism.", "The supplied Goal Contract is authoritative. Its target for each metric—not the parent value—is the QoR boundary. Do not silently turn a parent metric into a hard per-metric non-regression rule. A candidate may spend headroom on a metric already below its contract target only when it stays within that target, preserves integrity, and yields a strict improvement in the contract's normalized distance. Conversely, never recommend a candidate that leaves a target violation unresolved merely because it improves a local proxy.", "The Active Decision Stage is authoritative. In power_reclaim, apply its stated lexicographic leakage/dynamic residual rule exactly: when dynamic already meets its target, a candidate may use some of that dynamic headroom while it remains at or below the target and strictly improves unresolved leakage. Never require raw dynamic power to be lower than the parent in that situation. The official evaluator invokes top-level repair_power, parallel to repair_timing. Optimize leakage and dynamic power first and allow timing deterioration only within its stated safety ceiling. The verified hook identifies an entry point, not the entire permissible implementation: analyze the complete repair_power command chain and, when appropriate, implement a dedicated REPAIR_POWER clone/specialization of Setup/VT/size/buffer/parasitics/routing-related machinery. It must be invoked only by repair_power; ordinary repair_timing semantics must remain unchanged. In timing_recovery, rebuild the low-power state first, then use each Student's fixed named recipe. Compare each result only against the no-diff parent baseline for that exact recipe. Treat a high rate of power-cell-to-timing-cell reversions as evidence to refine the power reclaim policy, not a reason to hide the reversal.", "A controller revision named in the Active Decision Stage changes the experiment boundary. Do not use EPD outcomes produced under an older controller revision to suppress that named mechanism: treat it as a fresh activation experiment, require a new exact-recipe no-diff baseline, and allow the Student to edit its verified hook. This exception does not relax any QoR, source, guard, or integrity requirement.", "A supplied verified candidate slot is an executable controller allocation, not an invitation to revisit allocation. You must return an actionable source experiment for every supplied slot. Never change its claim into withhold/do-not-edit/do-not-run/remain-suppressed/retain-parent advice; if you believe the slot should not have been allocated, still preserve its executable claim and record that objection only in diagnosis_summary for the controller's next round.", "Do not use historical best scores or invent source paths. Preserve the fixed parent and benchmark boundary. The full EPD is an artifact pointer for audit, not text to reconstruct or speculate from.", "", "## Goal Contract", json.dumps(contract_view, ensure_ascii=False, indent=2), "", "## Active Decision Stage", json.dumps(decision_context or {"mode": "single_stage"}, ensure_ascii=False, indent=2), "", "## Parent", json.dumps(parent.to_dict(), ensure_ascii=False, indent=2), "", "## Diagnosis", json.dumps(diagnosis.to_dict(), ensure_ascii=False, indent=2), "", "## EPD (four program states; compact decision view)", json.dumps(epd, ensure_ascii=False, indent=2), "", "## Observation Memory", json.dumps(observations, ensure_ascii=False, indent=2), "", "## Timing Schedule / Cell-Reversal Memory", json.dumps(schedule_memory or {}, ensure_ascii=False, indent=2), "", "## Previous Teacher Review", json.dumps(previous_review, ensure_ascii=False, indent=2), "", "## Verified candidate slots", json.dumps([item.to_dict() for item in fallback], ensure_ascii=False, indent=2), "", "Return exactly one JSON object. It must contain `diagnosis_summary`, `parent_policy`, and `hypotheses`. The hypotheses array must have one entry for every supplied verified Student slot with `student_id`, `claim`, optional `source_hooks` restricted to the verified slot, optional `expected_signals` restricted to that slot, `falsification_condition`, and role one of `explore`, `refine`, `integrate`, `repair`. A sparse candidate set is intentional evidence exhaustion: do not invent or duplicate an extra slot. Each claim must be an actionable, bounded refinement of that Student's own verified mechanism family, source hooks, and expected signals; it must not import a separate mechanism from another Student slot or suppress an already allocated slot. Use EPD status: validated mechanisms can integrate, promising mechanisms can receive one bounded refinement, pending mechanisms need engineering repair, invalid mechanisms must not be repeated unchanged except when an authoritative controller revision explicitly names the mechanism. Treat an unactivated family/hook as an activation-calibration problem, an engineering failure as repair-only, and a fully evaluated refutation as suppressed unless the source hook or named controller revision changes."])

    @staticmethod
    def _review_prompt(
        *,
        parent: Parent,
        parent_after: Parent | None = None,
        promoted_student: str | None = None,
        decision_context: dict[str, object] | None = None,
        diagnosis: Diagnosis,
        epd: dict[str, object],
        observations: dict[str, object],
        schedule_memory: dict[str, object] | None = None,
        rows: Sequence[tuple[CandidateResult, EvidenceVerdict]],
    ) -> str:
        parent_after = parent_after or parent
        controller = {
            "parent_at_start": parent.to_dict(),
            "promoted_student": promoted_student,
            "parent_after": parent_after.to_dict(),
            "decision_context": decision_context or {},
            "authority": "deterministic_promotion_controller",
        }
        return "\n".join(["# GoalEvolve Persistent Teacher: post-round review", "", "You are reviewing completed Student evidence. Do not edit source. Reconcile final normalized distance, checkpoint debt, EPD status, observation memory, schedule evidence, and integrity gates. Runtime is execution telemetry only and must not affect any mechanism action or recommendation. Produce guidance for the next planning turn.", "The Controller Decision below is already committed and authoritative. Accept parent_after as the next lineage parent. A verified_qor_unattributed result may be promoted when it passed lineage admission even though a matched recipe baseline was unavailable; describe its source attribution as unresolved, but do not advise undoing that promotion. A cached failed exact-recipe baseline must not be rerun unchanged. Mechanism validation still requires matched attribution; lineage inheritance does not.", "Use the frozen contract residuals and Active Decision Stage exactly. Never call leakage dominant when TNS has the larger normalized residual, or vice versa. In adaptive_tradeoff, improve the named dominant residual and full distance while preserving already-satisfied targets; an unresolved secondary metric need not improve on every admitted timing step.", "", "## Controller Decision", json.dumps(controller, ensure_ascii=False, indent=2), "", "## Prior diagnosis", json.dumps(diagnosis.to_dict(), ensure_ascii=False, indent=2), "", "## Updated EPD", json.dumps(epd, ensure_ascii=False, indent=2), "", "## Observation Memory", json.dumps(observations, ensure_ascii=False, indent=2), "", "## Timing Schedule / Cell-Reversal Memory", json.dumps(schedule_memory or {}, ensure_ascii=False, indent=2), "", "## Structured Student feedback", json.dumps([_outcome(candidate, verdict) for candidate, verdict in rows], ensure_ascii=False, indent=2), "", "When a recipe has a high power-cell overlap or exact-reversion rate, record it explicitly as a repair_power follow-up constraint, but do not divert the current timing-recovery Student from its assigned repair_timing mechanism. Treat a missing phase signal as an activation/telemetry repair, not a QoR refutation.", "", "Return exactly one JSON object with `dominant_bottleneck`, `responsible_stage`, `retention_findings`, `mechanism_actions` (one per hypothesis with retain/refine/suppress/repair), `next_round_constraints`, and `parent_checkpoint_guidance`. Never call a local-only or verified_qor_unattributed mechanism validated."])


def _outcome(candidate: CandidateResult, verdict: EvidenceVerdict) -> dict[str, object]:
    return {"student_id": candidate.student_id, "hypothesis": candidate.hypothesis.to_dict(), "metrics": candidate.metrics, "phase_signals": candidate.phase_signals, "evaluation_error": candidate.evaluation_error, "verdict": verdict.to_dict(), "checkpoint_metrics": candidate.artifacts.get("checkpoint_metrics"), "official_4of4_log": candidate.artifacts.get("official_4of4_log")}
