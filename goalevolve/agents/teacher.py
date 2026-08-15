from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .codex_runtime import CodexRuntimeConfig, PersistentCodexRunner
from .markdown_protocol import (
    draft_signature_validation_errors,
    parse_draft_signatures,
    parse_teacher_plan,
    parse_teacher_review,
    render_teacher_plan,
    teacher_plan_validation_errors,
)
from .teacher_packet import TeacherPacketBuilder
from ..planning.diagnosis import Diagnosis
from ..planning.epd import EvolutionProgramDatabase
from ..core.models import CandidateResult, EvidenceVerdict, Hypothesis, Parent
from ..planning.observations import ObservationMemory
from ..planning.cross_design_experience import cross_design_experience_packet
from ..planning.timing_recovery import schedule_memory_summary, teacher_selectable_recipe_ids
from ..epd_search import build_explorer_retrieval_packet, validate_explorer_retrieval_audit


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
        repository_graph: dict[str, object] | None = None,
        search_policy: dict[str, object] | None = None,
        source_root: Path | None = None,
        paper_cards: Sequence[dict[str, object]] = (),
        historical_seeds: Sequence[dict[str, object]] = (),
        execution_contracts: dict[str, object] | None = None,
    ) -> TeacherPlan:
        epd = EvolutionProgramDatabase(state_root).teacher_summary()
        observations = ObservationMemory(state_root).summary()
        schedule_memory = schedule_memory_summary(state_root)
        cross_design_experience = cross_design_experience_packet(
            decision_context=decision_context,
        )
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
            repository_graph=repository_graph,
            search_policy=search_policy,
            source_root=source_root,
            paper_cards=paper_cards,
            historical_seeds=historical_seeds,
            execution_contracts=execution_contracts,
        )
        validation_attempts: list[dict[str, object]] = []
        required_roles = tuple(item.student_role for item in fallback)
        required_student_roles = {
            _student_id_from_hypothesis(item): item.student_role
            for item in fallback
            if _student_id_from_hypothesis(item)
        }
        seed_revalidation_options = {
            _student_id_from_hypothesis(item): dict(item.candidate_options[0])
            for item in fallback
            if item.student_role == "explorer"
            and item.role_mode == "seed_revalidation"
            and len(item.candidate_options) == 1
            and _student_id_from_hypothesis(item)
        }
        has_fresh_explorer_work = requires_fresh_explorer_work(fallback)
        requires_explorer_ideas = has_fresh_explorer_work
        requires_source_investigation = has_fresh_explorer_work
        draft_turn_artifacts: dict[str, str] = {}
        draft_signatures: tuple[dict[str, object], ...] = ()
        retrieval_packet: dict[str, object] = {}
        retrieval_audit: dict[str, object] = {}
        draft_errors: tuple[str, ...] = ()
        # Every turn receives the compact durable packet it needs.  Keep
        # remote conversations separate so exploratory source/tool transcripts
        # cannot eclipse the next turn's Controller evidence.
        if requires_explorer_ideas:
            draft_packet = self._draft_context_packet(
                contract=contract,
                parent=parent,
                diagnosis=diagnosis,
                decision_context=decision_context or {},
                repository_graph=repository_graph,
                source_root=source_root,
            )
            draft_turn = self.runner.run(
                state_root=state_root,
                identity=self._draft_identity(round_index),
                operation_id=f"r{round_index:03d}_teacher_draft_signatures",
                cwd=round_root,
                artifact_root=round_root / "teacher" / "draft_signatures",
                prompt=self._draft_signature_prompt(draft_packet=draft_packet),
            )
            draft_turn_artifacts = draft_turn.artifacts
            draft_markdown = self._read_markdown(draft_turn.artifacts.get("codex_last_message")) if draft_turn.ok else ""
            draft_signatures = parse_draft_signatures(draft_markdown) if draft_turn.ok else ()
            draft_errors = (
                draft_signature_validation_errors(draft_signatures)
                if draft_turn.ok
                else ("teacher_draft_signature_turn_failed",)
            )
            validation_attempts.append(
                {
                    "attempt": "draft_signatures",
                    "operation_id": draft_turn.operation_id,
                    "errors": list(draft_errors),
                }
            )
            if not draft_errors:
                trace_path = round_root / "teacher_epd_retrieval_trace.jsonl"
                retrieval_packet = build_explorer_retrieval_packet(
                    state_root=state_root,
                    signatures=draft_signatures,
                    trace_path=trace_path,
                )
                retrieval_audit = validate_explorer_retrieval_audit(
                    state_root=state_root,
                    signatures=draft_signatures,
                    trace_path=trace_path,
                )
                if not bool(retrieval_audit.get("accepted")):
                    draft_errors = tuple(
                        f"retrieval_audit_rejected:{signature_id}:{','.join(str(item) for item in list(row.get('errors') or ()))}"
                        for signature_id, row in dict(retrieval_audit.get("signatures") or {}).items()
                        if not bool(dict(row).get("accepted"))
                    )
                review_prompt = self._novelty_review_prompt(
                    planning_prompt=prompt,
                    draft_signatures=draft_signatures,
                    retrieval_packet=retrieval_packet,
                )
                turn = self.runner.run(
                    state_root=state_root,
                    identity=self._plan_identity(round_index),
                    operation_id=f"r{round_index:03d}_teacher_plan_novelty_review",
                    cwd=round_root,
                    artifact_root=round_root / "teacher" / "plan",
                    prompt=review_prompt,
                )
            else:
                turn = draft_turn
        else:
            turn = self.runner.run(
                state_root=state_root,
                identity=self._plan_identity(round_index),
                operation_id=f"r{round_index:03d}_teacher_plan",
                cwd=round_root,
                artifact_root=round_root / "teacher" / "plan",
                prompt=prompt,
            )
        markdown = self._read_markdown(turn.artifacts.get("codex_last_message")) if turn.ok and not draft_errors else ""
        errors = teacher_plan_validation_errors(
            markdown,
            required_roles=required_roles,
            required_student_roles=required_student_roles,
            require_explorer_ideas=requires_explorer_ideas,
            require_source_investigation=requires_source_investigation,
            require_evaluation_recipe=True,
        ) if turn.ok and not draft_errors else tuple(draft_errors or ("teacher_turn_failed",))
        teacher_failure_kind = "external_turn_failure" if not turn.ok else ""
        validation_attempts.append({"attempt": 0, "operation_id": turn.operation_id, "errors": list(errors)})
        initial_artifacts = turn.artifacts
        repair_turns: list[dict[str, str]] = []
        for repair_index in range(1, self.config.max_plan_format_repairs + 1):
            if not errors or teacher_failure_kind:
                break
            repair_prompt = self._plan_repair_prompt(
                prior_markdown=markdown,
                errors=errors,
                required_student_roles=required_student_roles,
                require_explorer_ideas=requires_explorer_ideas,
                allowed_recipe_ids=teacher_selectable_recipe_ids(
                    str((decision_context or {}).get("evaluation_mode") or "timing_only")
                ),
                execution_contracts=execution_contracts,
                seed_revalidation_options=seed_revalidation_options,
            )
            repaired = self.runner.run(
                state_root=state_root,
                identity=self._format_repair_identity(round_index, repair_index),
                operation_id=f"r{round_index:03d}_teacher_plan_format_repair_{repair_index:02d}",
                cwd=round_root,
                artifact_root=round_root / "teacher" / "plan" / f"format_repair_{repair_index:02d}",
                prompt=repair_prompt,
            )
            repair_turns.append(repaired.artifacts)
            if not repaired.ok:
                validation_attempts.append({"attempt": repair_index, "operation_id": repaired.operation_id, "errors": ["teacher_turn_failed"]})
                turn = repaired
                teacher_failure_kind = "external_turn_failure"
                errors = ("teacher_turn_failed",)
                break
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
                for artifacts in (draft_turn_artifacts, initial_artifacts, *repair_turns)
                if (path := artifacts.get("codex_events"))
            )
        )
        if turn.ok and requires_source_investigation and not bool(source_audit["satisfied"]):
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
            "teacher_failure_kind": teacher_failure_kind,
            "format_valid": not errors,
            "format_validation": validation_attempts,
            "format_repair_artifacts": repair_turns,
            "source_inspection_audit": source_audit,
            "draft_signatures": list(draft_signatures),
            "retrieval_packet": retrieval_packet,
            "retrieval_audit": retrieval_audit,
            "diagnosis": diagnosis.to_dict(),
            "epd": epd,
            "observations": observations,
            "cross_design_experience": cross_design_experience,
            "timing_schedule_memory": schedule_memory,
            "repository_graph": repository_graph or {},
            "search_policy": search_policy or {},
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
        cross_design_experience = cross_design_experience_packet(
            decision_context=decision_context,
        )
        prompt = self._review_prompt(
            parent=parent,
            parent_after=parent_after or parent,
            promoted_student=promoted_student,
            decision_context=decision_context,
            diagnosis=diagnosis,
            epd=epd,
            observations=observations,
            cross_design_experience=cross_design_experience,
            schedule_memory=schedule_memory,
            rows=rows,
        )
        turn = self.runner.run(state_root=state_root, identity=self._review_identity(round_index), operation_id=f"r{round_index:03d}_teacher_review", cwd=round_root, artifact_root=round_root / "teacher" / "review", prompt=prompt)
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
            "cross_design_experience": cross_design_experience,
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
        execution_contracts: dict[str, object] | None = None,
        retrieval_audit: Mapping[str, object] | None = None,
        decision_context: Mapping[str, object] | None = None,
        repair_index: int = 1,
    ) -> dict[str, object]:
        """Ask the same Teacher thread to correct deterministic admission errors."""
        required_student_roles = {
            _student_id_from_hypothesis(item): item.student_role
            for item in role_templates
            if _student_id_from_hypothesis(item)
        }
        require_explorer_ideas = requires_fresh_explorer_work(role_templates)
        seed_revalidation_options = {
            _student_id_from_hypothesis(item): dict(item.candidate_options[0])
            for item in role_templates
            if item.student_role == "explorer"
            and item.role_mode == "seed_revalidation"
            and len(item.candidate_options) == 1
            and _student_id_from_hypothesis(item)
        }
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
            execution_contracts=execution_contracts,
            seed_revalidation_options=seed_revalidation_options,
            retrieval_audit=retrieval_audit,
            cross_design_experience=cross_design_experience_packet(
                decision_context=decision_context,
            ),
        )
        turn = self.runner.run(
            state_root=state_root,
            identity=self._assignment_repair_identity(round_index, repair_index),
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
        # Controller repairs retain the same round and immutable parent
        # snapshot.  Include the initial planning turn and earlier repair
        # turns so a Markdown-only correction does not discard verified live
        # source investigation from this exact snapshot.
        source_audit = source_inspection_audit(
            tuple(
                path
                for path in sorted((round_root / "teacher" / "plan").glob("**/events.jsonl"))
                if path.is_file()
            )
        )
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
        """Return the stable namespace for a round's bounded turn identities."""
        return f"teacher_r{round_index:03d}"

    @staticmethod
    def _draft_identity(round_index: int) -> str:
        return f"{CodexTeacher._round_identity(round_index)}_draft"

    @staticmethod
    def _plan_identity(round_index: int) -> str:
        return f"{CodexTeacher._round_identity(round_index)}_plan"

    @staticmethod
    def _format_repair_identity(round_index: int, repair_index: int) -> str:
        return (
            f"{CodexTeacher._round_identity(round_index)}_"
            f"format_repair_{repair_index:02d}"
        )

    @staticmethod
    def _review_identity(round_index: int) -> str:
        return f"{CodexTeacher._round_identity(round_index)}_review"

    @staticmethod
    def _assignment_repair_identity(round_index: int, repair_index: int) -> str:
        """Keep deterministic repair packets out of the exploratory plan thread."""
        return (
            f"{CodexTeacher._round_identity(round_index)}_"
            f"assignment_repair_{repair_index:02d}"
        )

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
            activation = tuple(
                signal
                for signal in list(row.get("activation_signals") or [])
                if signal in expected
            ) or tuple(signal for signal in chosen.activation_signals if signal in expected)
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
            idea_reference = str(row.get("idea_reference") or "").strip()
            idea_record = dict(
                dict((teacher_context or {}).get("idea_records") or {}).get(
                    idea_reference, {}
                )
                or {}
            )
            assigned_idea = str(idea_record.get("idea") or "").strip()
            # Teacher can refine prose and select an EPD reference only within
            # the controller-created slot.  Role, source/evaluation boundary,
            # and the preselected eligible EPD IDs remain immutable.
            finalized = replace(
                    chosen,
                    claim=claim,
                    source_hooks=allowed_hooks,
                    expected_signals=expected,
                    activation_signals=activation,
                    epd_record_ids=selected_epd_ids,
                    teacher_idea_reference=(
                        idea_reference
                        if chosen.student_role == "explorer"
                        else ""
                    ),
                    scope_evidence=chosen.scope_evidence
                    + ((f"teacher_falsification:{str(row.get('falsification_condition') or '').strip()}",) if row.get("falsification_condition") else ()),
                    teacher_diagnosis_summary=str((teacher_context or {}).get("diagnosis_summary") or ""),
                    teacher_parent_policy=str((teacher_context or {}).get("parent_policy") or ""),
                    teacher_selection_rationale=str(row.get("selection_rationale") or "").strip(),
                    teacher_evolution_ideas=(assigned_idea,) if assigned_idea else (),
                    teacher_predicted_stage_effect=str(
                        idea_record.get("predicted_stage_effect") or ""
                    ),
                    teacher_internal_cpp_scheduling_suggestion=str(
                        row.get("internal_cpp_scheduling_suggestion")
                        or idea_record.get("internal_cpp_scheduling_suggestion")
                        or ""
                    ).strip(),
                )
            selected.append(finalized)
            selected_allocation_keys.add(_allocation_key(finalized))
        return selected

    @staticmethod
    def _draft_context_packet(
        *,
        contract,
        parent: Parent,
        diagnosis: Diagnosis,
        decision_context: Mapping[str, object],
        repository_graph: Mapping[str, object] | None,
        source_root: Path | None,
    ) -> str:
        contract_view = (
            contract.to_dict()
            if contract is not None and hasattr(contract, "to_dict")
            else {}
        )
        packet = TeacherPacketBuilder(
            contract=contract_view,
            parent=parent,
            diagnosis=diagnosis.to_dict(),
            epd={},
            observations={},
            schedule_memory={},
            previous_review={},
            slots=(),
            allowed_recipe_ids=(),
            decision_context=dict(decision_context),
            repository_graph=repository_graph,
            search_policy={},
            source_root=source_root,
            paper_cards=(),
            historical_seeds=(),
        )
        return "\n".join(packet.draft_sections())

    @staticmethod
    def _draft_signature_prompt(
        *,
        planning_prompt: str = "",
        draft_packet: str = "",
    ) -> str:
        context = draft_packet or CodexTeacher._draft_packet_projection(planning_prompt)
        return "\n".join(
            [
                "# Pass A: Draft mechanism signatures only",
                "Pass A supersedes every general EPD instruction below. Do not invoke `goalevolve.epd_search`, inspect its help or implementation, open EPD records, or perform historical retrieval in this pass; the Controller performs all five searches only after your output.",
                "Do not assign Students, decide promotion, or write a final idea. First make exactly two successful read-only `rg` or `sed` inspections of the live parent source, then form exactly five distinct Explorer draft signatures from the current bottleneck and those live-source facts. Each signature will be searched by the Controller before any final assignment is allowed.",
                context,
                "Return Markdown only, with exactly this schema:",
                "## Draft Mechanism Signatures",
                "### draft_1",
                "- Stage: <active stage>",
                "- Problem: <mechanism-level bottleneck>",
                "- Source Hook: <one live-source path>",
                "- Decision Type: <candidate admission|ranking|commit guard|rollback|other bounded boundary>",
                "- Observed State: <state read by the decision>",
                "- Action: <bounded proposed source action>",
                "- Guard: <falsification or preservation guard>",
                "- Expected Effect: <expected stage/final QoR effect>",
                "Repeat through `draft_5`. No additional headings or assignments.",
            ]
        )

    @staticmethod
    def _draft_packet_projection(planning_prompt: str) -> str:
        """Compatibility projection for callers that still supply full text."""
        headings = (
            "## Goal Contract",
            "## Active Decision Stage",
            "## Diagnosis",
            "## Live Source Access",
            "## Source Localization",
            "## P0-rooted Source Graph and Doc Cards",
            "## Controller Execution Contracts",
        )
        sections: list[str] = []
        for heading in headings:
            start = planning_prompt.find(heading)
            if start < 0:
                continue
            next_heading = re.search(r"\n## ", planning_prompt[start + len(heading):])
            end = (
                start + len(heading) + next_heading.start() + 1
                if next_heading is not None
                else len(planning_prompt)
            )
            section = planning_prompt[start:end].strip()
            if section:
                sections.append(section)
        return "\n\n".join(sections)

    @staticmethod
    def _novelty_review_prompt(
        *,
        planning_prompt: str,
        draft_signatures: Sequence[Mapping[str, object]],
        retrieval_packet: Mapping[str, object],
    ) -> str:
        compact_packet = [
            {
                "signature_id": row.get("signature_id"),
                "result_ids": [item.get("idea_id") for item in list(row.get("results") or ()) if isinstance(item, Mapping)],
                "opened_idea_ids": list(row.get("opened_idea_ids") or ()),
                "same_hook_boundary_ids": list(row.get("same_hook_boundary_ids") or ()),
            }
            for row in list(retrieval_packet.get("signatures") or ())
            if isinstance(row, Mapping)
        ]
        return "\n".join(
            [
                planning_prompt,
                "",
                "## Pass B: Controller retrieval and novelty review",
                "The Controller has already searched every Pass-A draft and opened top candidates plus all same-hook/same-boundary records. Use this evidence to remove or revise duplicates, then return the final Markdown plan in the exact schema above. Do not claim a search that is not shown here; open the path-routed retrieval packet if you need evidence detail.",
                f"Controller retrieval packet: {retrieval_packet.get('artifact_path') or '<not available>'}",
                "## Draft Signatures (Controller input)",
                json.dumps(list(draft_signatures), ensure_ascii=False, indent=2),
                "## Retrieval Summary (Controller input)",
                json.dumps(compact_packet, ensure_ascii=False, indent=2),
                "For every final Explorer idea, copy its Draft Signature ID and provide EPD Search Query, Retrieved Historical Ideas, Opened EPD Records, Nearest Historical Idea, Semantic Overlap, Material Difference, and Novelty Conclusion. Use `none` only when the Controller result set is empty. A different source hook or a materially different decision boundary is required to retain a near mechanism.",
            ]
        )

    @staticmethod
    def _plan_prompt(*, parent: Parent, diagnosis: Diagnosis, epd: dict[str, object], observations: dict[str, object], schedule_memory: dict[str, object] | None = None, previous_review: dict[str, object], fallback: Sequence[Hypothesis], contract=None, decision_context: dict[str, object] | None = None, source_index: dict[str, object] | None = None, repository_graph: dict[str, object] | None = None, search_policy: dict[str, object] | None = None, source_root: Path | None = None, paper_cards: Sequence[dict[str, object]] = (), historical_seeds: Sequence[dict[str, object]] = (), execution_contracts: dict[str, object] | None = None) -> str:
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
                "handoff evidence even when TNS is dominant; this does not change the Controller-provided role envelopes."
            )
        slots = [_assignment_from_hypothesis(item) for item in fallback]
        allowed_recipe_ids = teacher_selectable_recipe_ids(
            str(decision_context.get("evaluation_mode") or "timing_only")
        )
        requires_explorer_ideas = requires_fresh_explorer_work(fallback)
        requires_seed_revalidation = any(
            item.student_role == "explorer"
            and item.role_mode == "seed_revalidation"
            and item.candidate_options
            for item in fallback
        )
        packet = TeacherPacketBuilder(
            contract=contract_view,
            parent=parent,
            diagnosis=diagnosis.to_dict(),
            epd=epd,
            observations=observations,
            schedule_memory=schedule_memory or {},
            previous_review=previous_review,
            slots=slots,
            allowed_recipe_ids=allowed_recipe_ids,
            decision_context=decision_context,
            repository_graph=repository_graph,
            search_policy=search_policy or {},
            source_root=source_root,
            paper_cards=paper_cards,
            historical_seeds=historical_seeds,
        )
        active_power_boundary: list[str] = []
        source_localization = (
            "P0-rooted OpenROAD AST repository graph"
            if repository_graph is not None
            else "OpenROAD mechanism cards plus live-source inspection"
        )
        if (
            decision_context.get("stage") == "power_reclaim"
            and decision_context.get("evaluation_mode") == "power_only"
        ):
            phase = str(decision_context.get("power_reclaim_phase") or "<configured phase>")
            active_power_boundary = [
                "## Active Power Execution Boundary",
                "This is a hard Controller execution boundary. Name only a source hook that the exact command below can execute; a dormant helper is not a hook.",
                f"Exact command: repair_power -phase {phase}",
                "For early_forced_reclaim, a late-only symbol is inadmissible. To use late behavior, make the dispatch or phase transition itself the explicit executed source mechanism.",
                "",
            ]
        return "\n".join(
            [
                *packet.usage_guide(),
                "# GoalEvolve Persistent Teacher: diagnose and plan",
                "",
                f"You are the Teacher, with expertise in digital backend physical design. Your responsibility is to propose the next-round OpenROAD source-code algorithm modification directions, specifically for the RSZ and RMP source code used in the post-placement optimization stage, as well as optional Tcl scheduling recommendations for a goal-driven algorithm auto-evolution framework based on the current QoR bottlenecks. Diagnose the frozen-goal gap using only the compact decision packet, path-routed EPD evidence, parent checkpoint trajectory, {source_localization}, live source structure, paper-card references, and empirical observation memory. Afterwards, propose task directions for new-mechanism Explorers, promising-mechanism Enhancers, and validated-mechanism Integrators. You do not edit source code.",
                "The frozen QoR decision contract consists of exactly TNS, dynamic power, and leakage power. Runtime is execution telemetry only: never use it to choose, rank, retain, suppress, reject, or promote a mechanism. The Controller owns source validation, evaluation, and promotion. You own mechanism creation and task assignment. The Controller supplies role envelopes, not candidate mechanisms.",
                *( ["For each fresh Explorer, create a new algorithmic mechanism after at least two successful, read-only live-source rg/sed inspections and after the EPD draft-signature retrieval workflow."] if requires_explorer_ideas else [] ),
                *( ["This round contains only controller-bound seed revalidations. This instruction supersedes generic Explorer draft-signature, EPD-retrieval, and five-idea language in the compact packet: do not create Pass-A draft signatures, do not invoke EPD novelty retrieval, and do not manufacture five fresh Explorer ideas. Write one Evolution Idea for each assigned seed, retain its exact Candidate, source anchors, and activation signals, and let the Student inspect the live source plus the historical reference diff before making a fresh bounded diff."] if requires_seed_revalidation and not requires_explorer_ideas else [] ),
                "For Enhancer, explain the prior stage evidence and reinforce one bounded mechanism rather than restarting a failed patch. For Integrator, explain why all selected mechanism decision boundaries, read/write sets, guards, and rollback behavior coexist; explicitly leave the slot empty if no safe pair exists.",
                *( ["A `seed_revalidation` Explorer is a controller-scheduled source experiment: its Controller Role Envelope contains exactly one historical card. It must write that required controller seed ID in `Candidate`, inspect every listed live source anchor and reference diff, retain the card's activation signals, and create a fresh bounded diff against the current parent. The card is not a QoR result or promotion authority."] if requires_seed_revalidation else [] ),
                "You may propose an advisory internal-C++ scheduling recommendation, but external Tcl files and scripts must not be modified. A Tcl scheduling recommendation alone is not a new algorithmic mechanism and must be paired with a concrete source-level mechanism.",
                "Do not invent missing evidence, source symbols, execution paths, mechanism statuses, QoR results, or EPD records. Open path-routed evidence as needed before making an evidence claim.",
                "",
                *packet.sections(),
                *active_power_boundary,
                "## Controller Execution Contracts",
                "These controller-owned facts constrain execution and are not mechanism suggestions.",
                json.dumps(execution_contracts or {}, ensure_ascii=False, indent=2),
                "",
                "Return Markdown field blocks only. Do not return JSON or a code fence. Use exactly this structure:",
                "Source Hooks: relative source file paths only; never include ::symbol",
                "Source Evidence: path::symbol anchors; provide one or more anchors for every hook",
                "The Controller canonicalizes an accidental hook shorthand for recovery, but you must write the two fields correctly and every hook still needs verified matching evidence.",
                "",
                "## Diagnosis Summary",
                "<compact bottleneck diagnosis>",
                "",
                *(
                    [
                        "## Source Investigation",
                        "### investigation_1",
                        "- Source Evidence: <path::symbol inspected with rg/sed>",
                        "- Observed Control Point: <what the current parent actually does at this decision boundary>",
                        "",
                        "Provide at least two investigation blocks before the Evolution Ideas. These are evidence of reading the live parent source, not an implementation prescription.",
                        "",
                    ]
                    if requires_explorer_ideas
                    else []
                ),
                "## Evolution Ideas",
                "### idea_1",
                "- Idea: <one substantial paragraph: formation reason, core algorithm principle, target decision boundary and problem, expected stage/final effect, and bounded cross-file actions when needed>",
                "- Predicted Stage Effect: <expected phase/QoR movement>",
                "- Source Hooks: <relative source file path(s) only; never include ::symbol>",
                "- Source Evidence: <path::symbol anchor(s); one or more for every Source Hook>",
                "- Evaluation Recipe: <one controller recipe ID from the supplied menu>",
                "- Expected Signals: <new mechanism telemetry signal names>",
                "- Activation Signals: <nonempty subset of Expected Signals that proves the source-side mechanism boundary was entered; exclude optional/fallback outcome counters>",
                "- Falsification Condition: <official evidence condition>",
                (
                    "- Draft Signature: <Pass-A draft_N for a fresh Explorer; none for a seed_revalidation or EPD role>"
                    if requires_explorer_ideas
                    else "- Draft Signature: none"
                ),
                (
                    "- EPD Search Query: <same draft_N for a fresh Explorer; none for a seed_revalidation or EPD role>"
                    if requires_explorer_ideas
                    else "- EPD Search Query: none"
                ),
                "- Retrieved Historical Ideas: <comma-separated IDEA_* IDs from Controller results, or none>",
                "- Opened EPD Records: <comma-separated IDEA_* IDs opened by Controller, or none>",
                "- Nearest Historical Idea: <IDEA_* from Controller results, or none>",
                "- Semantic Overlap: <specific shared mechanism or none>",
                "- Material Difference: <specific distinct hook/state/boundary or no history>",
                "- Novelty Conclusion: <why retain, revise, or suppress this Explorer idea>",
                "- Paper Card References: <optional comma-separated card_id from supplied references, or none>",
                "- Priority: 0",
                "",
                (
                    "Provide at least five ranked Explorer ideas. Each must be a clear, substantive paragraph (roughly 1.5–2× the former terse idea), may span multiple source files, and must be a distinct falsifiable mechanism. Unselected ideas remain pending in EPD for a later Explorer."
                    if requires_explorer_ideas
                    else (
                        "For each seed_revalidation role envelope, provide exactly one substantive Evolution Idea and its matching Student Assignment. These are bounded revalidations, not suspended Explorers: preserve the exact controller Candidate, source anchors, Expected Signals, and Activation Signals."
                        if requires_seed_revalidation
                        else "Explorers are suspended for this one transition round. Do not invent Explorer ideas; focus on the scheduled EPD cleanup roles."
                    )
                ),
                "",
                "## Parent Policy",
                "<how the checked parent and frozen contract constrain this round>",
                "- Retire Pending Ideas: <optional comma-separated existing IDEA_* IDs that are pending and unexecuted, or none>",
                "",
                "## Student Assignments",
                "### student_1",
                "- Role: explorer",
                "- Candidate: <leave blank for an ordinary Explorer; for a seed_revalidation Explorer write its required controller seed ID; use an EPD option identifier only for an EPD role>",
                "- EPD Idea: idea_1",
                "- Claim: <bounded executable idea>",
                "- Selection Rationale: <why this is one of the best experiments now>",
                "- Source Hooks: <semicolon-separated real source paths>",
                "- Source Evidence: <semicolon-separated path::symbol anchors; use a full Doc Card declarator for overloads>",
                "- Evaluation Recipe: <the same controller recipe ID as its referenced idea>",
                "- Expected Signals: <comma-separated new telemetry signals>",
                "- Activation Signals: <exactly the same subset declared by its referenced idea>",
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
        execution_contracts: dict[str, object] | None = None,
        seed_revalidation_options: Mapping[str, Mapping[str, object]] | None = None,
        retrieval_audit: Mapping[str, object] | None = None,
        cross_design_experience: Mapping[str, object] | None = None,
    ) -> str:
        seed_revalidation_options = {
            str(student_id): dict(option)
            for student_id, option in dict(seed_revalidation_options or {}).items()
            if str(student_id) and isinstance(option, Mapping)
        }
        accepted_retrieval_bindings = {
            str(signature_id): {
                "result_ids": [
                    str(idea_id)
                    for idea_id in list(dict(row).get("result_ids") or ())
                    if str(idea_id)
                ],
                "opened_idea_ids": [
                    str(idea_id)
                    for idea_id in list(dict(row).get("opened_idea_ids") or ())
                    if str(idea_id)
                ],
            }
            for signature_id, row in dict(
                dict(retrieval_audit or {}).get("signatures") or {}
            ).items()
            if str(signature_id)
            and isinstance(row, Mapping)
            and bool(row.get("accepted"))
        }
        return "\n".join(
            [
                "# Teacher plan repair",
                "Your prior plan cannot be executed. Return a complete replacement Markdown plan, not commentary, JSON, or a code fence.",
                "Correct both structural and Controller source/history admission errors below. Do not weaken, omit, or rename required sections/roles. Retain valid technical reasoning where possible.",
                f"Required student roles: {json.dumps(required_student_roles, ensure_ascii=False)}",
                f"Require at least five Explorer ideas: {str(require_explorer_ideas).lower()}",
                f"Allowed evaluation recipes: {json.dumps(list(allowed_recipe_ids), ensure_ascii=False)}",
                "## Controller Execution Contracts",
                json.dumps(execution_contracts or {}, ensure_ascii=False, indent=2),
                "These are hard execution facts, not suggestions. For each assignment, choose a recipe from the allowed menu and name only Source Hooks that the corresponding controller execution contract actually reaches. A helper may be copied or specialized into an executed hook, but it is not itself a Source Hook unless the current flow dispatches it.",
                "Source Hooks: relative source file paths only; never include ::symbol",
                "Source Evidence: path::symbol anchors; provide one or more anchors for every hook",
                *(
                    [
                        "## Accepted Explorer Retrieval Bindings",
                        "For a fresh Explorer, retain one listed Draft Signature in both Draft Signature ID and EPD Search Query. Its Retrieved Historical Ideas, Opened EPD Records, and Nearest Historical Idea must use only the listed IDs. Do not write `none` when a listed signature has results, and do not invent an unlisted signature or ID.",
                        json.dumps(accepted_retrieval_bindings, ensure_ascii=False, indent=2),
                    ]
                    if accepted_retrieval_bindings
                    else []
                ),
                *(
                    [
                        "## Cross-Design Iteration Experience",
                        "These stage-relevant lessons constrain a repaired plan but never replace a required Controller binding, role, recipe, source boundary, or format field.",
                        json.dumps(
                            dict(cross_design_experience),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    ]
                    if cross_design_experience
                    else []
                ),
                *(
                    [
                        "## Required Seed Revalidation Assignments",
                        "Each listed Explorer must retain its exact controller card in Candidate, Source Hooks, Source Evidence, Expected Signals, and Activation Signals. Copy the card's exact expected_signals unchanged into both signal fields in its Evolution Idea and Student Assignment. It still needs a fresh diff and complete official evaluation; a seed is never historical QoR authority.",
                        json.dumps(seed_revalidation_options, ensure_ascii=False, indent=2),
                    ]
                    if seed_revalidation_options
                    else []
                ),
                "## Controller Validation Errors",
                *[f"- {error}" for error in errors],
                "## Prior Markdown",
                prior_markdown or "<no usable prior Markdown>",
                "## Required Format",
                "## Diagnosis Summary\n<text>\n\n## Source Investigation\n### investigation_1\n- Source Evidence: <path::symbol>\n- Observed Control Point: <text>\n\n## Evolution Ideas\n### idea_1\n- Idea: <text>\n- Predicted Stage Effect: <text>\n- Source Hooks: <path; another/path>\n- Source Evidence: <path::symbol; another/path::symbol>\n- Evaluation Recipe: <controller recipe ID from the prior plan>\n- Expected Signals: <signal>\n- Activation Signals: <subset of Expected Signals that proves entry, excluding optional outcome counters>\n- Falsification Condition: <text>\n- Draft Signature: <draft_N or none>\n- EPD Search Query: <draft_N or none>\n- Retrieved Historical Ideas: <IDEA_* IDs or none>\n- Opened EPD Records: <IDEA_* IDs or none>\n- Nearest Historical Idea: <IDEA_* or none>\n- Semantic Overlap: <text>\n- Material Difference: <text>\n- Novelty Conclusion: <text>\n- Paper Card References: none\n- Priority: 0\n\n## Parent Policy\n<text>\n- Retire Pending Ideas: none\n\n## Student Assignments\n### student_1\n- Role: explorer\n- Candidate: <required controller seed ID for a seed_revalidation Explorer; otherwise blank for Explorer>\n- EPD Idea: idea_1\n- Claim: <text>\n- Selection Rationale: <text>\n- Source Hooks: <path; another/path>\n- Source Evidence: <path::symbol; another/path::symbol>\n- Evaluation Recipe: <same controller recipe ID>\n- Expected Signals: <signal>\n- Activation Signals: <exactly the idea activation subset>\n- Falsification Condition: <text>\n- EPD References: none",
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
        cross_design_experience: Mapping[str, object] | None = None,
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
        return "\n".join([
            "# GoalEvolve Persistent Teacher: post-round review",
            "",
            "You are reviewing completed Student evidence. Do not edit source. The Controller Decision is committed and authoritative. Runtime is execution telemetry only and never a QoR decision signal. Do not call local-only or verified_qor_unattributed evidence validated. Empty `hypothesis.allowed_patch_paths` means no exact-file fence; it is not an allocation failure. Use Controller `verdict.integrity_ok`, `evaluation_error`, and `preflight` evidence for allocation or preflight failures. When `integrity_ok` and `mechanism_fired` are true and `distance_gain` is zero or negative, classify it as `fully_evaluated_qor_refutation`.",
            "",
            "## Controller Decision", json.dumps(controller, ensure_ascii=False, indent=2),
            "",
            "## Prior Diagnosis", json.dumps(diagnosis.to_dict(), ensure_ascii=False, indent=2),
            "",
            "## Updated EPD", json.dumps(_review_epd_projection(epd), ensure_ascii=False, indent=2),
            "",
            "## Observation Memory", json.dumps(_review_observation_projection(observations), ensure_ascii=False, indent=2),
            "",
            "## Cross-Design Iteration Experience", json.dumps(
                dict(cross_design_experience or {}), ensure_ascii=False, indent=2
            ),
            "",
            "## Timing Schedule / Cell-Reversal Memory", json.dumps(_review_schedule_projection(schedule_memory or {}), ensure_ascii=False, indent=2),
            "",
            "## Structured Student Feedback", json.dumps([_review_outcome(candidate, verdict) for candidate, verdict in rows], ensure_ascii=False, indent=2),
            "",
            "Return Markdown field blocks only. Do not return JSON or a code fence. Use exactly this structure:",
            "",
            "## Round Assessment", "<what was learned from all four Students and EPD state transitions>",
            "",
            "## Mechanism Actions", "### <mechanism_family>",
            "- Action: retain | refine | suppress | repair",
            "- Evidence Classification: <validated_official_gain | fully_evaluated_qor_refutation | activation_failure_engineering_exhausted | pending_engineering | other>",
            "- Rationale: <bounded evidence-grounded explanation>",
            "",
            "Repeat for evaluated mechanisms only.",
            "",
            "## Next Round Constraints", "<compact constraints for new explorers, EPD integration, and EPD enhancement>",
        ])


def _outcome(candidate: CandidateResult, verdict: EvidenceVerdict) -> dict[str, object]:
    return {"student_id": candidate.student_id, "hypothesis": candidate.hypothesis.to_dict(), "metrics": candidate.metrics, "phase_signals": candidate.phase_signals, "evaluation_error": candidate.evaluation_error, "verdict": verdict.to_dict(), "preflight": candidate.artifacts.get("preflight"), "checkpoint_metrics": candidate.artifacts.get("checkpoint_metrics"), "official_4of4_log": candidate.artifacts.get("official_4of4_log")}


def _review_outcome(candidate: CandidateResult, verdict: EvidenceVerdict) -> dict[str, object]:
    """Project durable outcome facts without replaying source or peer menus."""
    hypothesis = candidate.hypothesis
    return {
        "student_id": candidate.student_id,
        "hypothesis": {
            "hypothesis_id": hypothesis.hypothesis_id,
            "mechanism_family": hypothesis.mechanism_family,
            "role": hypothesis.student_role,
            "role_mode": hypothesis.role_mode,
            "claim": hypothesis.claim[:320],
            "source_hooks": list(hypothesis.source_hooks),
            "expected_signals": list(hypothesis.expected_signals),
        },
        "metrics": candidate.metrics,
        "phase_signals": candidate.phase_signals,
        "evaluation_error": candidate.evaluation_error,
        "verdict": verdict.to_dict(),
        "preflight": candidate.artifacts.get("preflight"),
        "checkpoint_metrics": candidate.artifacts.get("checkpoint_metrics"),
        "official_4of4_log": candidate.artifacts.get("official_4of4_log"),
    }


def _review_epd_projection(epd: Mapping[str, object]) -> dict[str, object]:
    records = [
        {
            key: row.get(key)
            for key in ("record_id", "idea_id", "mechanism_family", "epd_status", "evidence_state", "goal_distance", "distance_gain")
            if key in row
        }
        for row in list(epd.get("decision_records") or ())[:12]
        if isinstance(row, Mapping)
    ]
    return {
        "status_counts": dict(epd.get("status_counts") or {}),
        "recent_decision_records": records,
        "full_epd_artifact": epd.get("full_epd_artifact") or "knowledge/epd.json",
    }


def _review_observation_projection(observations: Mapping[str, object]) -> dict[str, object]:
    return {
        "observation_count": observations.get("observation_count"),
        "family_hook_summary": list(observations.get("family_hook_summary") or ())[:10],
        "full_observation_memory": observations.get("full_observation_memory") or "knowledge/observations.json",
    }


def _review_schedule_projection(schedule_memory: Mapping[str, object]) -> dict[str, object]:
    return {
        "full_schedule_memory": schedule_memory.get("full_schedule_memory") or schedule_memory.get("full_schedule_memory_artifact") or "knowledge/timing_schedule_memory.json",
        "recipe_rollup": dict(schedule_memory.get("recipe_rollup") or {}),
        "recent": list(schedule_memory.get("recent") or schedule_memory.get("active_rules") or ())[:10],
    }


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
            if not re.search(r"(?:src/(?:rsz|rmp|grt)/|\.cc\b|\.hh\b|\.hpp\b|\.h\b)", command):
                continue
            commands.append(command)
    return {
        "successful_source_commands": len(commands),
        "commands": commands[-16:],
        "satisfied": len(commands) >= 2,
    }


def requires_fresh_explorer_work(hypotheses: Iterable[Hypothesis]) -> bool:
    """Whether this round has an Explorer that must author a novel mechanism.

    A ``seed_revalidation`` slot is already bounded by a Controller-selected
    historical card.  It still produces a fresh Student diff, but it is not a
    request for fresh mechanism discovery, Pass-A signatures, or EPD novelty
    retrieval.
    """

    return any(
        hypothesis.student_role == "explorer"
        and hypothesis.role_mode != "seed_revalidation"
        for hypothesis in hypotheses
    )


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
        "activation_signals": hypothesis.activation_signals,
        "epd_record_ids": hypothesis.epd_record_ids,
        "epd_idea_id": hypothesis.epd_idea_id,
        "idea_reference": hypothesis.teacher_idea_reference,
        "role_mode": hypothesis.role_mode,
        "candidate_options": [
            {
                "candidate_id": option.get("candidate_id")
                or option.get("hypothesis_id")
                or next(iter(option.get("retrieval_ids") or ()), ""),
                "seed_id": option.get("seed_id"),
                "mechanism_family": option.get("mechanism_family"),
                "source_hooks": option.get("source_hooks"),
                "source_anchors": option.get("source_anchors"),
                "decision_boundary": option.get("decision_boundary"),
                "summary": option.get("summary"),
                "expected_signals": option.get("expected_signals"),
                "reference_diff_paths": option.get("reference_diff_paths"),
                "activation_signals": option.get("activation_signals"),
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
    # Controller candidate options may carry immutable allocation metadata
    # (for example ``candidate_id`` and ``source_anchors``).  Those fields
    # are selection/audit data, not constructor arguments; only the
    # Hypothesis schema may cross this boundary.
    normalized = {
        key: value
        for key, value in dict(option).items()
        if key in Hypothesis.__dataclass_fields__
    }
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
