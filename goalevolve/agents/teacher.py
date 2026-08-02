from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

from .codex_runtime import CodexRuntimeConfig, PersistentCodexRunner
from .markdown_protocol import (
    parse_teacher_plan,
    parse_teacher_review,
    render_teacher_plan,
    teacher_plan_validation_errors,
)
from ..planning.diagnosis import Diagnosis
from ..planning.epd import EvolutionProgramDatabase
from ..core.models import CandidateResult, EvidenceVerdict, Hypothesis, Parent
from ..planning.observations import ObservationMemory
from ..planning.timing_recovery import schedule_memory_summary, teacher_selectable_recipe_ids


@dataclass(frozen=True)
class CodexTeacherConfig:
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "xhigh"
    retries: int = 3
    timeout_s: int = 3600
    seed_home: Path = Path("outputs/codex_home")
    credential_env: Path | None = None
    max_plan_format_repairs: int = 2


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
        assignments = [_assignment_from_hypothesis(item) for item in fallback]
        markdown = render_teacher_plan(
            diagnosis_summary="Deterministic fallback: execute the source-verified role allocation.",
            parent_policy="Keep the checked parent; the controller owns promotion.",
            evolution_ideas=("Retain independently source-verified mechanisms for this bootstrap round.",),
            assignments=assignments,
        )
        parsed = parse_teacher_plan(markdown)
        hypotheses = CodexTeacher._sanitize_hypotheses(
            parsed.get("assignments"),
            fallback,
            teacher_context=_teacher_context(parsed),
        )
        return TeacherPlan(
            tuple(hypotheses),
            diagnosis,
            {
                "schema_version": "goalevolve.v2.teacher_plan.v2",
                "planner_mode": "heuristic",
                "diagnosis": diagnosis.to_dict(),
                "teacher_markdown": markdown,
                "parsed_markdown": parsed,
                "hypotheses": [item.to_dict() for item in hypotheses],
            },
            {},
        )

    def review(self, *, diagnosis: Diagnosis, rows: Sequence[tuple[CandidateResult, EvidenceVerdict]], **_: object) -> dict[str, object]:
        markdown = "\n".join(
            [
                "## Round Assessment",
                "Deterministic fallback review; retain only controller-verified evidence.",
                "",
                "## Mechanism Actions",
                "",
                "## Next Round Constraints",
                "Keep the frozen contract and independent promotion gate.",
            ]
        )
        return {
            "schema_version": "goalevolve.v2.teacher_review.v2",
            "planner_mode": "heuristic",
            "diagnosis": diagnosis.to_dict(),
            "teacher_markdown": markdown,
            "parsed_markdown": parse_teacher_review(markdown),
            "outcomes": [_outcome(candidate, verdict) for candidate, verdict in rows],
        }


class CodexTeacher:
    """Persistent Teacher that diagnoses EPD/checkpoint evidence and plans Students."""

    name = "codex_teacher"

    def __init__(self, config: CodexTeacherConfig) -> None:
        self.config = config
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
        source_index: dict[str, object] | None = None,
        source_root: Path | None = None,
        paper_cards: Sequence[dict[str, object]] = (),
    ) -> TeacherPlan:
        epd = EvolutionProgramDatabase(state_root).teacher_summary()
        observations = ObservationMemory(state_root).summary()
        schedule_memory = schedule_memory_summary(state_root)
        prompt = self._plan_prompt(
            contract=contract,
            parent=parent,
            diagnosis=diagnosis,
            epd=epd,
            observations=observations,
            schedule_memory=schedule_memory,
            previous_review=previous_review,
            fallback=fallback,
            decision_context=decision_context,
            source_index=source_index,
            source_root=source_root,
            paper_cards=paper_cards,
        )
        # Planning and review for one round share compact local context; the
        # next round starts a clean thread because EPD/review are supplied in
        # its packet.  This bounds remote conversation-token accumulation.
        turn = self.runner.run(state_root=state_root, identity=self._round_identity(round_index), operation_id=f"r{round_index:03d}_teacher_plan", cwd=round_root, artifact_root=round_root / "teacher" / "plan", prompt=prompt)
        markdown = self._read_markdown(turn.artifacts.get("codex_last_message")) if turn.ok else ""
        validation_attempts: list[dict[str, object]] = []
        required_roles = tuple(item.student_role for item in fallback)
        required_student_roles = {
            _student_id_from_hypothesis(item): item.student_role
            for item in fallback
            if _student_id_from_hypothesis(item)
        }
        requires_explorer_ideas = any(role == "explorer" for role in required_roles)
        requires_source_investigation = requires_explorer_ideas
        errors = teacher_plan_validation_errors(
            markdown,
            required_roles=required_roles,
            required_student_roles=required_student_roles,
            require_explorer_ideas=requires_explorer_ideas,
            require_source_investigation=requires_source_investigation,
            require_evaluation_recipe=True,
        ) if turn.ok else ("teacher_turn_failed",)
        validation_attempts.append({"attempt": 0, "operation_id": turn.operation_id, "errors": list(errors)})
        repair_turns: list[dict[str, str]] = []
        for repair_index in range(1, self.config.max_plan_format_repairs + 1):
            if not errors:
                break
            repair_prompt = self._plan_repair_prompt(
                prior_markdown=markdown,
                errors=errors,
                required_student_roles=required_student_roles,
                require_explorer_ideas=requires_explorer_ideas,
                allowed_recipe_ids=teacher_selectable_recipe_ids(
                    str((decision_context or {}).get("evaluation_mode") or "timing_only")
                ),
            )
            repaired = self.runner.run(
                state_root=state_root,
                identity=self._round_identity(round_index),
                operation_id=f"r{round_index:03d}_teacher_plan_format_repair_{repair_index:02d}",
                cwd=round_root,
                artifact_root=round_root / "teacher" / "plan" / f"format_repair_{repair_index:02d}",
                prompt=repair_prompt,
            )
            repair_turns.append(repaired.artifacts)
            if not repaired.ok:
                validation_attempts.append({"attempt": repair_index, "operation_id": repaired.operation_id, "errors": ["teacher_turn_failed"]})
                continue
            markdown = self._read_markdown(repaired.artifacts.get("codex_last_message"))
            errors = teacher_plan_validation_errors(
                markdown,
                required_roles=required_roles,
                required_student_roles=required_student_roles,
                require_explorer_ideas=requires_explorer_ideas,
                require_source_investigation=requires_source_investigation,
                require_evaluation_recipe=True,
            )
            validation_attempts.append({"attempt": repair_index, "operation_id": repaired.operation_id, "errors": list(errors)})
            turn = repaired
        source_audit = source_inspection_audit(
            tuple(
                Path(path)
                for artifacts in (turn.artifacts, *repair_turns)
                if (path := artifacts.get("codex_events"))
            )
        )
        if requires_source_investigation and not bool(source_audit["satisfied"]):
            errors = tuple([*errors, "teacher_source_inspection_not_observed"])
        parsed = parse_teacher_plan(markdown) if not errors else {}
        # Materialization belongs to the Controller.  For legacy/heuristic
        # callers this compatibility fallback remains, but the engine's Codex
        # path replaces these envelopes with controller-validated assignments.
        hypotheses = self._sanitize_hypotheses(
            parsed.get("assignments"), fallback, teacher_context=_teacher_context(parsed)
        ) if not errors else []
        plan = {
            "schema_version": "goalevolve.v2.teacher_plan.v2",
            "teacher_ok": turn.ok,
            "teacher_detail": turn.detail,
            "format_valid": not errors,
            "format_validation": validation_attempts,
            "format_repair_artifacts": repair_turns,
            "source_inspection_audit": source_audit,
            "diagnosis": diagnosis.to_dict(),
            "epd": epd,
            "observations": observations,
            "timing_schedule_memory": schedule_memory,
            "previous_review": previous_review,
            "teacher_markdown": markdown,
            "parsed_markdown": parsed,
            "hypotheses": [item.to_dict() for item in hypotheses],
        }
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
        markdown = self._read_markdown(turn.artifacts.get("codex_last_message")) if turn.ok else ""
        parsed = parse_teacher_review(markdown)
        return {
            "schema_version": "goalevolve.v2.teacher_review.v2",
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
            "teacher_markdown": markdown,
            "parsed_markdown": parsed,
            # Keep this compatibility key controller-generated so old state
            # readers continue to work; Codex itself never emits JSON here.
            "mechanism_actions": parsed.get("mechanism_actions") or [],
            "artifacts": turn.artifacts,
        }

    def repair_plan_after_controller_validation(
        self,
        *,
        state_root: Path,
        round_root: Path,
        round_index: int,
        prior_markdown: str,
        errors: Sequence[str],
        role_templates: Sequence[Hypothesis],
        repair_index: int = 1,
    ) -> dict[str, object]:
        """Ask the same Teacher thread to correct deterministic admission errors."""
        required_student_roles = {
            _student_id_from_hypothesis(item): item.student_role
            for item in role_templates
            if _student_id_from_hypothesis(item)
        }
        require_explorer_ideas = any(item.student_role == "explorer" for item in role_templates)
        prompt = self._plan_repair_prompt(
            prior_markdown=prior_markdown,
            errors=errors,
            required_student_roles=required_student_roles,
            require_explorer_ideas=require_explorer_ideas,
            allowed_recipe_ids=tuple(
                sorted(
                    {
                        recipe_id
                        for template in role_templates
                        for recipe_id in teacher_selectable_recipe_ids(template.evaluation_mode)
                    }
                )
            ),
        )
        turn = self.runner.run(
            state_root=state_root,
            identity=self._round_identity(round_index),
            operation_id=f"r{round_index:03d}_teacher_assignment_repair_{repair_index:02d}",
            cwd=round_root,
            artifact_root=round_root / "teacher" / "plan" / f"assignment_repair_{repair_index:02d}",
            prompt=prompt,
        )
        markdown = self._read_markdown(turn.artifacts.get("codex_last_message")) if turn.ok else ""
        structural_errors = teacher_plan_validation_errors(
            markdown,
            required_roles=tuple(item.student_role for item in role_templates),
            required_student_roles=required_student_roles,
            require_explorer_ideas=require_explorer_ideas,
            require_source_investigation=require_explorer_ideas,
            require_evaluation_recipe=True,
        ) if turn.ok else ("teacher_turn_failed",)
        source_audit = source_inspection_audit(
            (Path(turn.artifacts["codex_events"]),)
        ) if turn.artifacts.get("codex_events") else {"satisfied": False, "successful_source_commands": 0, "commands": []}
        if require_explorer_ideas and not bool(source_audit["satisfied"]):
            structural_errors = tuple([*structural_errors, "teacher_source_inspection_not_observed"])
        return {
            "teacher_ok": turn.ok,
            "teacher_detail": turn.detail,
            "teacher_markdown": markdown,
            "parsed_markdown": parse_teacher_plan(markdown) if not structural_errors else {},
            "format_errors": list(structural_errors),
            "source_inspection_audit": source_audit,
            "repair_index": repair_index,
            "artifacts": turn.artifacts,
        }

    @staticmethod
    def _read_markdown(path_value: str | None) -> str:
        if not path_value:
            return ""
        try:
            return Path(path_value).read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            return ""

    @staticmethod
    def _round_identity(round_index: int) -> str:
        """Keep plan/review continuity within, but never across, a round."""
        return f"teacher_r{round_index:03d}"

    @staticmethod
    def _sanitize_hypotheses(
        raw: object,
        fallback: Sequence[Hypothesis],
        *,
        teacher_context: dict[str, object] | None = None,
    ) -> list[Hypothesis]:
        rows = [item for item in list(raw or []) if isinstance(item, dict)]
        selected: list[Hypothesis] = []
        selected_allocation_keys: set[str] = set()
        for base in fallback:
            row = next((item for item in rows if str(item.get("student_id") or "") == _student_id_from_hypothesis(base)), {})
            candidate_id = str(row.get("candidate_id") or "").strip()
            chosen = next(
                (
                    _hypothesis_from_option(option)
                    for option in base.candidate_options
                    if candidate_id
                    and candidate_id
                    in {
                        str(option.get("hypothesis_id") or ""),
                        *[str(item) for item in list(option.get("retrieval_ids") or ())],
                    }
                ),
                base,
            )
            if chosen.student_id != base.student_id or chosen.hypothesis_id != base.hypothesis_id:
                chosen = replace(
                    chosen,
                    hypothesis_id=base.hypothesis_id,
                    student_id=base.student_id,
                    student_role=base.student_role,
                )
            # The two explorer slots are an independent search pair.  A
            # duplicate Teacher selection would spend two full flows on the
            # same mechanism, so retain this slot's deterministic default
            # whenever the requested candidate has already been allocated.
            if _allocation_key(chosen) in selected_allocation_keys:
                chosen = _first_unallocated_option(base, selected_allocation_keys)
            allowed_hooks = tuple(hook for hook in list(row.get("source_hooks") or []) if hook in chosen.source_hooks) or chosen.source_hooks
            expected = tuple(signal for signal in list(row.get("expected_signals") or []) if signal in chosen.expected_signals) or chosen.expected_signals
            claim = str(row.get("claim") or chosen.claim).strip()
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
                claim = chosen.claim
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
            own_markers = {Path(hook).stem for hook in chosen.source_hooks}
            if any(marker in claim for marker in other_markers - own_markers):
                claim = chosen.claim
            requested_epd_ids = tuple(
                record_id
                for record_id in list(row.get("epd_record_ids") or ())
                if str(record_id) in chosen.epd_record_ids
            )
            # An integrator candidate is an atomic, controller-vetted pair.
            # Allowing Markdown to remove one parent silently turns crossover
            # into enhancement, while retaining the misleading role label.
            # The Teacher chooses a different pair through Candidate instead.
            selected_epd_ids = (
                chosen.epd_record_ids
                if chosen.student_role == "integrator"
                and chosen.role_mode == "epd_integration"
                else (requested_epd_ids or chosen.epd_record_ids)
            )
            # Teacher can refine prose and select an EPD reference only within
            # the controller-created slot.  Role, source/evaluation boundary,
            # and the preselected eligible EPD IDs remain immutable.
            finalized = replace(
                    chosen,
                    claim=claim,
                    source_hooks=allowed_hooks,
                    expected_signals=expected,
                    epd_record_ids=selected_epd_ids,
                    teacher_idea_reference=(
                        str(row.get("idea_reference") or "").strip()
                        if chosen.student_role == "explorer"
                        else ""
                    ),
                    scope_evidence=chosen.scope_evidence
                    + ((f"teacher_falsification:{str(row.get('falsification_condition') or '').strip()}",) if row.get("falsification_condition") else ()),
                    teacher_diagnosis_summary=str((teacher_context or {}).get("diagnosis_summary") or ""),
                    teacher_parent_policy=str((teacher_context or {}).get("parent_policy") or ""),
                    teacher_selection_rationale=str(row.get("selection_rationale") or "").strip(),
                    teacher_evolution_ideas=tuple(
                        str(item)
                        for item in list((teacher_context or {}).get("evolution_ideas") or ())
                        if str(item).strip()
                    ),
                    teacher_predicted_stage_effect=str(
                        dict((teacher_context or {}).get("idea_records") or {}).get(
                            str(row.get("idea_reference") or ""),
                            {},
                        ).get("predicted_stage_effect")
                        or ""
                    ),
                )
            selected.append(finalized)
            selected_allocation_keys.add(_allocation_key(finalized))
        return selected

    @staticmethod
    def _plan_prompt(*, parent: Parent, diagnosis: Diagnosis, epd: dict[str, object], observations: dict[str, object], schedule_memory: dict[str, object] | None = None, previous_review: dict[str, object], fallback: Sequence[Hypothesis], contract=None, decision_context: dict[str, object] | None = None, source_index: dict[str, object] | None = None, source_root: Path | None = None, paper_cards: Sequence[dict[str, object]] = ()) -> str:
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
                "preserve all already-satisfied targets, and use the exact recipe no-diff baseline. Keep the "
                "candidate menu diverse across dominant-residual, repair_power-durability, and power-to-timing "
                "handoff evidence even when TNS is dominant; this does not change the four Student roles."
            )
        slots = [_assignment_from_hypothesis(item) for item in fallback]
        allowed_recipe_ids = teacher_selectable_recipe_ids(
            str(decision_context.get("evaluation_mode") or "timing_only")
        )
        requires_explorer_ideas = any(item.student_role == "explorer" for item in fallback)
        return "\n".join(
            [
                "# GoalEvolve Persistent Teacher: diagnose and plan",
                "",
                "You are the Teacher. Diagnose the frozen-goal gap using only the supplied compact EPD, parent checkpoint trajectory, live source structure, paper-card references, and empirical observation memory. You do not edit source code.",
                "The frozen QoR decision contract is exactly TNS, dynamic power, and leakage power. Runtime is execution telemetry only: never use it to choose, rank, retain, suppress, or promote a mechanism. The controller owns source validation, evaluation, and promotion.",
                "You own mechanism creation and assignment. The Controller supplies role envelopes, not candidate mechanisms. For every Explorer, inspect the live source with at least two successful rg/sed commands, locate a real hook, and create a new bounded idea. Do not copy, paraphrase, or treat a paper card as a candidate mechanism: it is only topical context. Avoid every invalid, validated, and promising Explorer family in EPD unless both the source hook and the decision boundary are materially different. For an Integrator, choose a compatible historical pair from the supplied EPD options and explain how each decision boundary will coexist in the current parent. For an Enhancer, choose one promising EPD attempt and explain the stage evidence that prevented stable post-route gain before proposing one bounded strengthening. You may devise the implementation approach, but must name real source paths and symbols. Never change Tcl or benchmark inputs.",
                "",
                "## Goal Contract",
                json.dumps(contract_view, ensure_ascii=False, indent=2),
                "",
                "## Active Decision Stage",
                json.dumps(decision_context or {"mode": "single_stage"}, ensure_ascii=False, indent=2),
                "",
                "## Parent",
                json.dumps(parent.to_dict(), ensure_ascii=False, indent=2),
                "",
                "## Diagnosis",
                json.dumps(diagnosis.to_dict(), ensure_ascii=False, indent=2),
                "",
                "## EPD (idea lifecycle and compact attempts)",
                json.dumps(epd, ensure_ascii=False, indent=2),
                "",
                "## Observation Memory",
                json.dumps(observations, ensure_ascii=False, indent=2),
                "",
                "## Timing Schedule / Cell-Reversal Memory",
                json.dumps(schedule_memory or {}, ensure_ascii=False, indent=2),
                "",
                "## Previous Teacher Review",
                json.dumps(previous_review, ensure_ascii=False, indent=2),
                "",
                "## Controller Role Envelopes",
                json.dumps(slots, ensure_ascii=False, indent=2),
                "The Controller owns Tcl and exposes only these evaluation recipes. Select exactly one recipe per idea and assignment; the same recipe is used for that Student and its no-diff parent baseline. Do not invent a recipe or modify Tcl.",
                json.dumps(allowed_recipe_ids, ensure_ascii=False),
                "",
                "## Live Source Access",
                f"Read-only parent source root: {source_root or '<not supplied>'}",
                "Use rg and bounded local reads against that directory before choosing hooks. The Controller checks every `path::symbol` anchor below against this exact snapshot.",
                "",
                "## Compact Source Structure Index",
                json.dumps(source_index or {}, ensure_ascii=False, indent=2),
                "",
                "## Paper Card References",
                json.dumps(list(paper_cards), ensure_ascii=False, indent=2),
                "These are topics only. Their IDs may be cited, but they do not authorize reuse of an old patch recipe.",
                "",
                "Return Markdown field blocks only. Do not return JSON or a code fence. Use exactly this structure:",
                "",
                "## Diagnosis Summary",
                "<compact bottleneck diagnosis>",
                "",
                "## Source Investigation",
                "### investigation_1",
                "- Source Evidence: <path::symbol inspected with rg/sed>",
                "- Observed Control Point: <what the current parent actually does at this decision boundary>",
                "",
                "Provide at least two investigation blocks before the Evolution Ideas. These are evidence of reading the live parent source, not an implementation prescription.",
                "",
                "## Evolution Ideas",
                "### idea_1",
                "- Idea: <bounded source-level idea>",
                "- Predicted Stage Effect: <expected phase/QoR movement>",
                "- Source Hooks: <supplied controller hook(s)>",
                "- Source Evidence: <path::symbol, one anchor for every Source Hook>",
                "- Evaluation Recipe: <one controller recipe ID from the supplied menu>",
                "- Expected Signals: <new mechanism telemetry signal names>",
                "- Falsification Condition: <official evidence condition>",
                "- Paper Card References: <optional comma-separated card_id from supplied references, or none>",
                "- Priority: 0",
                "",
                ("Provide at least five ranked Explorer ideas. Unselected ideas remain pending in EPD for a later Explorer." if requires_explorer_ideas else "Explorers are suspended for this one transition round. Do not invent Explorer ideas; focus on the scheduled EPD cleanup roles."),
                "",
                "## Parent Policy",
                "<how the checked parent and frozen contract constrain this round>",
                "- Retire Pending Ideas: <optional comma-separated existing IDEA_* IDs that are pending and unexecuted, or none>",
                "",
                "## Student Assignments",
                "### student_1",
                "- Role: explorer",
                "- Candidate: <leave blank for Explorer; use an EPD option identifier only for an EPD role>",
                "- EPD Idea: idea_1",
                "- Claim: <bounded executable idea>",
                "- Selection Rationale: <why this is one of the best experiments now>",
                "- Source Hooks: <comma-separated real source paths>",
                "- Source Evidence: <comma-separated path::symbol anchors>",
                "- Evaluation Recipe: <the same controller recipe ID as its referenced idea>",
                "- Expected Signals: <comma-separated new telemetry signals>",
                "- Falsification Condition: <official evidence condition>",
                "- EPD References: none",
                "",
                "Repeat one `### student_N` block for every supplied role envelope. Preserve each supplied Role. An Explorer's EPD Idea must reference one of this response's idea_N blocks. EPD References must exactly match one listed integrator pair or enhancer record. The Controller validates paths, symbols, EPD references, and Explorer novelty after this response.",
            ]
        )

    @staticmethod
    def _plan_repair_prompt(
        *,
        prior_markdown: str,
        errors: Sequence[str],
        required_student_roles: dict[str, str],
        require_explorer_ideas: bool,
        allowed_recipe_ids: Sequence[str],
    ) -> str:
        return "\n".join(
            [
                "# Teacher plan repair",
                "Your prior plan cannot be executed. Return a complete replacement Markdown plan, not commentary, JSON, or a code fence.",
                "Correct both structural and Controller source/history admission errors below. Do not weaken, omit, or rename required sections/roles. Retain valid technical reasoning where possible.",
                f"Required student roles: {json.dumps(required_student_roles, ensure_ascii=False)}",
                f"Require at least five Explorer ideas: {str(require_explorer_ideas).lower()}",
                f"Allowed evaluation recipes: {json.dumps(list(allowed_recipe_ids), ensure_ascii=False)}",
                "## Controller Validation Errors",
                *[f"- {error}" for error in errors],
                "## Prior Markdown",
                prior_markdown or "<no usable prior Markdown>",
                "## Required Format",
                "## Diagnosis Summary\n<text>\n\n## Source Investigation\n### investigation_1\n- Source Evidence: <path::symbol>\n- Observed Control Point: <text>\n\n## Evolution Ideas\n### idea_1\n- Idea: <text>\n- Predicted Stage Effect: <text>\n- Source Hooks: <path>\n- Source Evidence: <path::symbol>\n- Evaluation Recipe: <controller recipe ID from the prior plan>\n- Expected Signals: <signal>\n- Falsification Condition: <text>\n- Paper Card References: none\n- Priority: 0\n\n## Parent Policy\n<text>\n- Retire Pending Ideas: none\n\n## Student Assignments\n### student_1\n- Role: explorer\n- Candidate: \n- EPD Idea: idea_1\n- Claim: <text>\n- Selection Rationale: <text>\n- Source Hooks: <path>\n- Source Evidence: <path::symbol>\n- Evaluation Recipe: <same controller recipe ID>\n- Expected Signals: <signal>\n- Falsification Condition: <text>\n- EPD References: none",
            ]
        )

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
        return "\n".join(["# GoalEvolve Persistent Teacher: post-round review", "", "You are reviewing completed Student evidence. Do not edit source. The Controller Decision is committed and authoritative. Runtime is execution telemetry only and never a QoR decision signal. Do not call local-only or verified_qor_unattributed evidence validated.", "", "## Controller Decision", json.dumps(controller, ensure_ascii=False, indent=2), "", "## Prior Diagnosis", json.dumps(diagnosis.to_dict(), ensure_ascii=False, indent=2), "", "## Updated EPD", json.dumps(epd, ensure_ascii=False, indent=2), "", "## Observation Memory", json.dumps(observations, ensure_ascii=False, indent=2), "", "## Timing Schedule / Cell-Reversal Memory", json.dumps(schedule_memory or {}, ensure_ascii=False, indent=2), "", "## Structured Student Feedback", json.dumps([_outcome(candidate, verdict) for candidate, verdict in rows], ensure_ascii=False, indent=2), "", "Return Markdown field blocks only. Do not return JSON or a code fence. Use exactly this structure:", "", "## Round Assessment", "<what was learned from all four Students and EPD state transitions>", "", "## Mechanism Actions", "### <mechanism_family>", "- Action: retain | refine | suppress | repair", "- Evidence Classification: <validated_official_gain | fully_evaluated_qor_refutation | activation_failure_engineering_exhausted | pending_engineering | other>", "- Rationale: <bounded evidence-grounded explanation>", "", "Repeat for evaluated mechanisms only.", "", "## Next Round Constraints", "<compact constraints for new explorers, EPD integration, and EPD enhancement>"])


def _outcome(candidate: CandidateResult, verdict: EvidenceVerdict) -> dict[str, object]:
    return {"student_id": candidate.student_id, "hypothesis": candidate.hypothesis.to_dict(), "metrics": candidate.metrics, "phase_signals": candidate.phase_signals, "evaluation_error": candidate.evaluation_error, "verdict": verdict.to_dict(), "checkpoint_metrics": candidate.artifacts.get("checkpoint_metrics"), "official_4of4_log": candidate.artifacts.get("official_4of4_log")}


def source_inspection_audit(event_paths: Iterable[Path]) -> dict[str, object]:
    """Summarize successful Teacher source reads recorded by Codex JSON events.

    Codex is free to choose its own source-navigation commands.  The
    Controller only requires evidence that it actually inspected the live
    snapshot before submitting Explorer assignments.
    """
    commands: list[str] = []
    for path in event_paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = dict(event.get("item") or {})
            if event.get("type") != "item.completed" or item.get("type") != "command_execution":
                continue
            if item.get("exit_code") != 0:
                continue
            command = str(item.get("command") or "")
            if not re.search(r"\b(?:rg|sed|awk|head|tail)\b", command):
                continue
            if not re.search(r"(?:src/(?:rsz|rmp|grt)/|\.cc\b|\.hh\b|\.hpp\b)", command):
                continue
            commands.append(command)
    return {
        "successful_source_commands": len(commands),
        "commands": commands[-16:],
        "satisfied": len(commands) >= 2,
    }


def _student_id_from_hypothesis(hypothesis: Hypothesis) -> str:
    if hypothesis.student_id:
        return hypothesis.student_id
    import re

    match = re.search(r"(?:^|_)student_(\d+)(?:_|$)", hypothesis.hypothesis_id)
    return f"student_{match.group(1)}" if match else ""


def _assignment_from_hypothesis(hypothesis: Hypothesis) -> dict[str, object]:
    return {
        "student_id": _student_id_from_hypothesis(hypothesis),
        "role": hypothesis.student_role,
        "candidate_id": hypothesis.hypothesis_id,
        "evaluation_recipe": hypothesis.timing_recipe_id,
        "claim": hypothesis.claim,
        "source_hooks": hypothesis.source_hooks,
        "expected_signals": hypothesis.expected_signals,
        "epd_record_ids": hypothesis.epd_record_ids,
        "epd_idea_id": hypothesis.epd_idea_id,
        "idea_reference": hypothesis.teacher_idea_reference,
        "role_mode": hypothesis.role_mode,
        "candidate_options": [
            {
                "candidate_id": option.get("hypothesis_id")
                or next(iter(option.get("retrieval_ids") or ()), ""),
                "mechanism_family": option.get("mechanism_family"),
                "source_hooks": option.get("source_hooks"),
                "role_mode": option.get("role_mode"),
                "epd_record_ids": option.get("epd_record_ids"),
                "epd_idea_id": option.get("epd_idea_id"),
            }
            for option in hypothesis.candidate_options
        ],
    }


def _hypothesis_from_option(option: dict[str, object]) -> Hypothesis:
    tuple_fields = {
        "source_hooks",
        "expected_signals",
        "retrieval_ids",
        "scope_evidence",
        "allowed_patch_paths",
        "activation_signals",
        "conclusive_nonactivation_patterns",
        "epd_record_ids",
        "teacher_evolution_ideas",
    }
    normalized = dict(option)
    for field in tuple_fields:
        if field in normalized:
            normalized[field] = tuple(normalized[field] or ())
    if "candidate_options" in normalized:
        normalized["candidate_options"] = tuple(dict(item) for item in normalized["candidate_options"] or ())
    return Hypothesis(**normalized)


def _teacher_context(parsed: dict[str, object]) -> dict[str, object]:
    return {
        "diagnosis_summary": str(parsed.get("diagnosis_summary") or ""),
        "parent_policy": str(parsed.get("parent_policy") or ""),
        "evolution_ideas": tuple(
            str(item) for item in list(parsed.get("evolution_ideas") or ()) if str(item).strip()
        ),
        "idea_records": {
            str(record.get("reference") or ""): dict(record)
            for record in list(parsed.get("evolution_idea_records") or ())
            if isinstance(record, dict) and str(record.get("reference") or "")
        },
    }


def _allocation_key(hypothesis: Hypothesis) -> str:
    if hypothesis.role_mode.startswith(("fresh", "bootstrap")):
        return "fresh:" + "|".join((*hypothesis.retrieval_ids, *hypothesis.source_hooks))
    return f"{hypothesis.student_role}:" + "|".join(hypothesis.epd_record_ids or hypothesis.retrieval_ids)


def _first_unallocated_option(base: Hypothesis, allocated: set[str]) -> Hypothesis:
    for option in base.candidate_options:
        candidate = _hypothesis_from_option(option)
        candidate = replace(
            candidate,
            hypothesis_id=base.hypothesis_id,
            student_id=base.student_id,
            student_role=base.student_role,
        )
        if _allocation_key(candidate) not in allocated:
            return candidate
    return base
