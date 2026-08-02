from __future__ import annotations

import json
import shutil
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.contracts import GoalContract
from ..planning.diagnosis import diagnose
from ..planning.epd import EvolutionProgramDatabase
from ..core.io import atomic_json, load_json, sha256_json
from ..core.models import CandidateResult, EvidenceVerdict, Hypothesis, Parent
from ..planning.observations import ObservationMemory
from ..planning.repository_graph import RepositoryGraphIndex
from ..planning.search_policy import SearchPolicyBuilder
from ..core.plugins import Evaluator, Planner, PromotionPolicy, StudentEditor, Teacher, WorkspaceProvider
from ..agents.prompting import review_packet, student_packet, teacher_packet
from .preflight import preflight_candidate
from ..token_ledger import record_round_token_usage
from ..planning.timing_recovery import record_schedule_memory, timing_recipe
from .workspace import clone_source_tree
from .teacher_assignment import (
    build_role_templates,
    materialize_teacher_assignments,
)
from ..evaluation.leaderboard import update_unified_leaderboard


@dataclass
class GoalEvolveEngine:
    contract: GoalContract
    state_root: Path
    planner: Planner
    evaluator: Evaluator
    workspace_provider: WorkspaceProvider
    promotion_policy: PromotionPolicy
    student_ids: tuple[str, ...] = ("student_1", "student_2", "student_3", "student_4")
    workers: int = 4
    student_editor: StudentEditor | None = None
    teacher: Teacher | None = None
    max_campaign_rounds: int | None = None
    prefer_execution_champion: bool = False
    epd_max_reinforcement_attempts: int = 2

    def _epd(self) -> EvolutionProgramDatabase:
        return EvolutionProgramDatabase(
            self.state_root,
            max_reinforcement_attempts=self.epd_max_reinforcement_attempts,
        )

    def initialize(self, *, baseline_metrics: dict[str, float], source_commit: str = "baseline", source_hash: str = "baseline") -> Parent:
        self.state_root.mkdir(parents=True, exist_ok=True)
        existing = load_json(self.state_root / "contract.json")
        if isinstance(existing, dict):
            existing_contract = GoalContract.from_dict(existing)
            if existing_contract.contract_id != self.contract.contract_id:
                raise RuntimeError("state root already has a different frozen goal contract")
            print(f"[GoalEvolve][campaign] resume contract={existing_contract.contract_id}", flush=True)
            parent = self._load_parent()
            self._epd().ensure_baseline(parent)
            return parent
        distance, _, _ = self.contract.evaluate(baseline_metrics)
        parent = Parent("baseline", dict(baseline_metrics), source_commit, source_hash, distance)
        atomic_json(self.state_root / "contract.json", self.contract.to_dict())
        atomic_json(self.state_root / "parent.json", parent.to_dict())
        atomic_json(self.state_root / "plugins.json", {"planner": self.planner.name, "teacher": self.teacher.name if self.teacher else "none", "student_editor": self.student_editor.name if self.student_editor else "none", "evaluator": self.evaluator.name, "workspace": self.workspace_provider.name})
        self._epd().ensure_baseline(parent)
        print(f"[GoalEvolve][campaign] initialized contract={self.contract.contract_id} baseline_distance={distance:.8f}", flush=True)
        return parent

    def run(self, *, rounds: int, baseline_metrics: dict[str, float] | None = None) -> Parent:
        parent = self._load_parent() if (self.state_root / "parent.json").is_file() else self.initialize(baseline_metrics=baseline_metrics or self.contract.baseline_metrics)
        reclassified = self._epd().reclassify_historical_unactivated_attempts()
        if reclassified:
            print(f"[GoalEvolve][epd] reclassified_unactivated={len(reclassified)}", flush=True)
        parent = self._adopt_execution_champion(parent)
        start = self._last_round() + 1
        for round_index in range(start, start + rounds):
            if self.max_campaign_rounds is not None and round_index > self.max_campaign_rounds:
                break
            parent = self.run_round(round_index=round_index, parent=parent)
            parent = self._adopt_execution_champion(parent)
            completion = self._completion_reason(round_index=round_index)
            if completion is None and self.max_campaign_rounds is not None and round_index >= self.max_campaign_rounds:
                completion = {
                    "reason": "campaign_round_limit_reached",
                    "round": round_index,
                    "max_campaign_rounds": self.max_campaign_rounds,
                }
            if completion is not None:
                atomic_json(self.state_root / "completion.json", completion)
                print(
                    f"[GoalEvolve][campaign] stop reason={completion['reason']} round={round_index}",
                    flush=True,
                )
                break
        return parent

    def _completion_reason(self, *, round_index: int) -> dict[str, object] | None:
        """Stop only on a complete official candidate, never a local proxy."""
        round_root = self.state_root / "rounds" / f"round_{round_index:03d}"
        for candidate_path in sorted(round_root.glob("students/*/artifacts/candidate.json")):
            candidate = load_json(candidate_path, {}) or {}
            if candidate.get("evaluation_error"):
                continue
            checks = {
                str(item.get("name") or ""): bool(item.get("passed"))
                for item in list(candidate.get("checks") or [])
                if isinstance(item, Mapping)
            }
            if not all(checks.get(name, False) for name in ("build", "flow", "metrics", "lec")):
                continue
            metrics = dict(candidate.get("metrics") or {})
            try:
                if float(metrics.get("drv_count", float("inf"))) != 0.0:
                    continue
            except (TypeError, ValueError):
                continue
            distance, _, missing = self.contract.evaluate(metrics)
            if not missing and distance <= 1e-12:
                return {
                    "reason": "goal_contract_met",
                    "all_satisfied_conditions": ["goal_contract_met"],
                    "round": round_index,
                    "student_id": str(candidate.get("student_id") or candidate_path.parents[1].name),
                    "candidate": str(candidate_path),
                    "metrics": metrics,
                    "goal_distance": distance,
                }
        return None

    @staticmethod
    def _matching_teacher_idea(
        *,
        epd_database: EvolutionProgramDatabase,
        idea_id: str,
        hypothesis: Hypothesis,
    ) -> str:
        """Bind an Explorer only to a pending idea with the same source fence."""
        if not idea_id or hypothesis.student_role != "explorer":
            return ""
        try:
            idea = epd_database.idea(idea_id)
        except KeyError:
            return ""
        hooks = tuple(str(path) for path in list(idea.get("source_hooks") or ()) if path)
        signals = tuple(str(signal) for signal in list(idea.get("expected_signals") or ()) if signal)
        if not hooks or not signals:
            return ""
        if not set(hooks).issubset(hypothesis.source_hooks):
            return ""
        if not set(signals).issubset(hypothesis.expected_signals):
            return ""
        return idea_id

    def _teacher_role_schedule(
        self,
        *,
        round_index: int,
        diagnosis,
        portfolio: Mapping[str, object],
        decision_context: Mapping[str, object],
    ) -> tuple[tuple[Hypothesis, ...], dict[str, object]]:
        """Schedule role envelopes without allocating mechanisms to them."""
        previous = self._previous_round_diagnosis(round_index)
        previous_bottleneck = str(previous.get("dominant_bottleneck") or "")
        current_bottleneck = str(getattr(diagnosis, "dominant_bottleneck", "") or "")
        changed = bool(previous_bottleneck and current_bottleneck and previous_bottleneck != current_bottleneck)
        templates = build_role_templates(
            student_ids=self.student_ids,
            round_index=round_index,
            decision_context=decision_context,
            portfolio=portfolio,
            suspend_explorers=changed,
        )
        if not templates and not changed:
            # An empty initial EPD still needs fresh experiments. This branch
            # is defensive for profiles with fewer than two configured IDs.
            templates = build_role_templates(
                student_ids=self.student_ids,
                round_index=round_index,
                decision_context=decision_context,
                portfolio={},
                suspend_explorers=False,
            )
        schedule = {
            "schema_version": "goalevolve.v2.role-schedule.v1",
            "previous_dominant_bottleneck": previous_bottleneck or None,
            "dominant_bottleneck": current_bottleneck or None,
            "bottleneck_changed": changed,
            "explorers_suspended": changed,
            "roles": [
                {
                    "student_id": item.student_id,
                    "role": item.student_role,
                    "role_mode": item.role_mode,
                    "epd_options": [dict(option) for option in item.candidate_options],
                }
                for item in templates
            ],
        }
        return templates, schedule

    def _previous_round_diagnosis(self, round_index: int) -> dict[str, object]:
        if round_index <= 1:
            return {}
        payload = load_json(
            self.state_root / "rounds" / f"round_{round_index - 1:03d}" / "round.json",
            {},
        ) or {}
        diagnosis = payload.get("diagnosis") if isinstance(payload, Mapping) else {}
        return dict(diagnosis) if isinstance(diagnosis, Mapping) else {}

    @staticmethod
    def _teacher_reference_symptoms(
        *,
        diagnosis,
        decision_context: Mapping[str, object],
    ) -> tuple[str, ...]:
        stage = str(decision_context.get("stage") or "")
        if stage == "power_reclaim":
            return ("leakage", "dynamic", "power", "power_reclaim")
        if stage in {"timing_recovery", "adaptive_tradeoff"}:
            return ("tns", "timing", "timing_recovery")
        bottleneck = str(getattr(diagnosis, "dominant_bottleneck", "") or "")
        return tuple(dict.fromkeys((bottleneck.split("_")[0], "timing", "power")))

    def run_round(self, *, round_index: int, parent: Parent) -> Parent:
        context_factory = getattr(self.promotion_policy, "context", None)
        decision_context = (
            context_factory(
                contract=self.contract,
                parent=parent,
                round_index=round_index,
            )
            if callable(context_factory)
            else {"mode": "single_stage"}
        )
        print(f"[GoalEvolve][round={round_index:03d}][teacher] planning parent={parent.parent_id} distance={parent.goal_distance:.8f}", flush=True)
        ensure_parent = getattr(self.workspace_provider, "ensure_parent", None)
        if callable(ensure_parent):
            ensure_parent(state_root=self.state_root, parent=parent)
        parent = self._stage_matched_parent(
            parent=parent,
            decision_context=decision_context,
        )
        # Stage matching can change the measured operating point without
        # changing the inherited source.  Recompute the controller context
        # from that official measurement, then let an exact same-source
        # recipe frontier refine *planning* only.  The lineage parent below
        # remains authoritative for source inheritance and promotion.
        if callable(context_factory):
            decision_context = context_factory(
                contract=self.contract,
                parent=parent,
                round_index=round_index,
            )
        decision_context = self._adaptive_frontier_context(
            parent=parent,
            decision_context=decision_context,
        )
        # A stage baseline is an immutable parent measurement, not a Student
        # result.  All candidates in this round must compare against it.
        parent_at_start = parent
        round_root = self.state_root / "rounds" / f"round_{round_index:03d}"
        round_root.mkdir(parents=True, exist_ok=True)
        parent_checkpoints = self._parent_checkpoints(parent)
        round_diagnosis = diagnose(contract=self.contract, parent=parent, checkpoints=parent_checkpoints)
        atomic_json(round_root / "diagnosis.json", round_diagnosis.to_dict())
        retrieval_audit = getattr(self.planner, "retriever", None)
        audit = retrieval_audit.audit(state_root=self.state_root) if retrieval_audit else {}
        # A completed Student flow is much more expensive than the controller
        # bookkeeping that follows it.  If the process died after evaluation
        # (for example while classifying a failed exact-recipe baseline), use
        # the immutable Teacher allocation already written for this round.
        # Replanning here could assign a different source mechanism to the
        # existing workspace and would destroy both attribution and paid work.
        saved_teacher_plan = self._incomplete_teacher_plan(round_root)
        teacher_plan = None
        teacher_plan_payload: dict[str, object] = {}
        if saved_teacher_plan is not None:
            hypotheses, teacher_plan_payload = saved_teacher_plan
            print(
                f"[GoalEvolve][round={round_index:03d}][recovery] "
                f"reuse_teacher_plan students={len(hypotheses)}",
                flush=True,
            )
        else:
            previous_review = self._previous_teacher_review(round_index, parent=parent)
        epd_database = self._epd()
        epd_portfolio = epd_database.role_portfolio(
            contract=self.contract,
            parent=parent,
        )
        atomic_json(round_root / "epd_role_portfolio.json", epd_portfolio)
        if saved_teacher_plan is None:
            if getattr(self.teacher, "name", "") == "codex_teacher":
                role_templates, role_schedule = self._teacher_role_schedule(
                    round_index=round_index,
                    diagnosis=round_diagnosis,
                    portfolio=epd_portfolio,
                    decision_context=decision_context,
                )
                if not role_templates:
                    raise RuntimeError("teacher_role_schedule_has_no_actionable_students")
                parent_source = self.state_root / "parents" / parent.source_hash / "source"
                allowed_patch_roots = tuple(
                    getattr(self.evaluator, "config", object()).allowed_patch_roots
                ) if hasattr(getattr(self.evaluator, "config", object()), "allowed_patch_roots") else ()
                repository_graph = RepositoryGraphIndex(state_root=self.state_root).build_parent(
                    source_root=parent_source,
                    source_hash=parent.source_hash,
                    allowed_patch_roots=allowed_patch_roots,
                )
                source_index = repository_graph.compact_index()
                repository_graph_packet = repository_graph.focus(
                    metric_hints=self._teacher_reference_symptoms(
                        diagnosis=round_diagnosis,
                        decision_context=decision_context,
                    ),
                    allowed_patch_roots=allowed_patch_roots,
                )
                search_policy = SearchPolicyBuilder(self.state_root).build(
                    parent=parent,
                    diagnosis=round_diagnosis,
                    epd_portfolio=epd_portfolio,
                    repository_graph=repository_graph,
                    allowed_patch_roots=allowed_patch_roots,
                )
                SearchPolicyBuilder.persist(round_root=round_root, policy=search_policy)
                retriever = getattr(self.planner, "retriever", None)
                paper_cards = (
                    retriever.paper_card_references(
                        parent=parent,
                        symptoms=self._teacher_reference_symptoms(
                            diagnosis=round_diagnosis,
                            decision_context=decision_context,
                        ),
                        state_root=self.state_root,
                    )
                    if retriever is not None and hasattr(retriever, "paper_card_references")
                    else []
                )
                teacher_plan = self.teacher.plan(
                    state_root=self.state_root,
                    round_root=round_root,
                    round_index=round_index,
                    contract=self.contract,
                    parent=parent,
                    diagnosis=round_diagnosis,
                    fallback=role_templates,
                    previous_review=previous_review,
                    decision_context=decision_context,
                    source_index=source_index,
                    repository_graph=repository_graph_packet,
                    search_policy=search_policy,
                    source_root=parent_source,
                    paper_cards=paper_cards,
                )
                teacher_plan_payload = teacher_plan.plan
                if not bool(teacher_plan_payload.get("format_valid")):
                    raise RuntimeError("teacher_markdown_format_invalid_after_repair")
                parsed_plan = dict(teacher_plan_payload.get("parsed_markdown") or {})
                def materialize(plan: Mapping[str, object]):
                    return materialize_teacher_assignments(
                        assignments=tuple(
                            item for item in list(plan.get("assignments") or ()) if isinstance(item, Mapping)
                        ),
                        evolution_ideas=tuple(
                            item for item in list(plan.get("evolution_idea_records") or ()) if isinstance(item, Mapping)
                        ),
                        templates=role_templates,
                        source_root=parent_source,
                        allowed_patch_roots=allowed_patch_roots,
                        historical_ideas=epd_database.ideas(),
                        paper_card_ids=tuple(str(row.get("card_id") or "") for row in paper_cards),
                        repository_graph=repository_graph,
                        teacher_context={
                            "diagnosis_summary": plan.get("diagnosis_summary"),
                            "parent_policy": plan.get("parent_policy"),
                        },
                    )

                materialized = materialize(parsed_plan)
                repair_budget = max(
                    1,
                    int(getattr(getattr(self.teacher, "config", None), "max_plan_format_repairs", 1)),
                )
                controller_repairs: list[dict[str, object]] = []
                prior_markdown = str(teacher_plan_payload.get("teacher_markdown") or "")
                controller_errors = list(materialized.errors)
                for repair_index in range(1, repair_budget + 1):
                    if not controller_errors:
                        break
                    repaired = self.teacher.repair_plan_after_controller_validation(
                        state_root=self.state_root,
                        round_root=round_root,
                        round_index=round_index,
                        prior_markdown=prior_markdown,
                        errors=controller_errors,
                        role_templates=role_templates,
                        repair_index=repair_index,
                    )
                    controller_repairs.append(repaired)
                    if not bool(repaired.get("teacher_ok")):
                        controller_errors = ["teacher_assignment_repair_turn_failed"]
                        break
                    format_errors = [str(item) for item in list(repaired.get("format_errors") or ())]
                    if format_errors:
                        controller_errors = format_errors
                        prior_markdown = str(repaired.get("teacher_markdown") or prior_markdown)
                        continue
                    parsed_plan = dict(repaired.get("parsed_markdown") or {})
                    materialized = materialize(parsed_plan)
                    controller_errors = list(materialized.errors)
                    prior_markdown = str(repaired.get("teacher_markdown") or prior_markdown)
                    if not controller_errors:
                        teacher_plan_payload["teacher_markdown"] = prior_markdown
                        teacher_plan_payload["parsed_markdown"] = parsed_plan
                if controller_repairs:
                    teacher_plan_payload["controller_assignment_repair"] = controller_repairs[-1]
                    teacher_plan_payload["controller_assignment_repairs"] = controller_repairs
                if controller_errors:
                    teacher_plan_payload["controller_assignment_errors"] = controller_errors
                    atomic_json(round_root / "teacher_plan.json", teacher_plan_payload)
                    raise RuntimeError("teacher_assignment_rejected_after_repair:" + ";".join(controller_errors))
                hypotheses = list(materialized.hypotheses)
                teacher_plan_payload["hypotheses"] = [item.to_dict() for item in hypotheses]
                teacher_plan_payload["role_schedule"] = role_schedule
                teacher_plan_payload["source_index"] = source_index
                teacher_plan_payload["repository_graph"] = repository_graph_packet
                teacher_plan_payload["search_policy"] = search_policy
                teacher_plan_payload["paper_cards"] = paper_cards
                cited_cards = [
                    str(card_id)
                    for idea in list(parsed_plan.get("evolution_idea_records") or ())
                    if isinstance(idea, Mapping)
                    for card_id in list(idea.get("paper_card_ids") or ())
                    if str(card_id)
                ]
                if retriever is not None and hasattr(retriever, "record_paper_card_references"):
                    teacher_plan_payload["paper_card_usage"] = retriever.record_paper_card_references(
                        state_root=self.state_root,
                        round_index=round_index,
                        card_ids=cited_cards,
                    )
            else:
                fallback_hypotheses = self.planner.plan(
                    contract=self.contract,
                    parent=parent,
                    round_index=round_index,
                    student_ids=self.student_ids,
                    state_root=self.state_root,
                    diagnosis=round_diagnosis,
                    decision_context=decision_context,
                )
                hypotheses = list(fallback_hypotheses)
                if self.teacher is not None:
                    teacher_plan = self.teacher.plan(
                        state_root=self.state_root,
                        round_root=round_root,
                        round_index=round_index,
                        contract=self.contract,
                        parent=parent,
                        diagnosis=round_diagnosis,
                        fallback=fallback_hypotheses,
                        previous_review=previous_review,
                        decision_context=decision_context,
                    )
                    hypotheses = list(teacher_plan.hypotheses)
                    teacher_plan_payload = teacher_plan.plan
            if teacher_plan_payload:
                atomic_json(round_root / "teacher_plan.json", teacher_plan_payload)
                markdown = str(teacher_plan_payload.get("teacher_markdown") or "")
                if markdown:
                    (round_root / "teacher_plan.md").write_text(markdown + "\n", encoding="utf-8")
                atomic_json(
                    round_root / "teacher_plan.parsed.json",
                    dict(teacher_plan_payload.get("parsed_markdown") or {}),
                )
        idea_references: dict[str, str] = {}
        if teacher_plan_payload:
            parsed_plan = dict(teacher_plan_payload.get("parsed_markdown") or {})
            teacher_plan_payload["retired_pending_idea_ids"] = list(
                epd_database.retire_pending_ideas(
                    idea_ids=tuple(str(item) for item in list(parsed_plan.get("retire_pending_ideas") or ())),
                )
            )
            registered_ideas = epd_database.register_teacher_ideas(
                round_index=round_index,
                parent=parent,
                evolution_ideas=tuple(
                    item
                    for item in list(parsed_plan.get("evolution_idea_records") or ())
                    if isinstance(item, Mapping) and str(item.get("idea") or "").strip()
                ) or tuple(
                    {"reference": f"idea_{index}", "idea": str(item), "priority": index - 1}
                    for index, item in enumerate(list(parsed_plan.get("evolution_ideas") or ()), start=1)
                    if str(item).strip()
                ),
                diagnosis_summary=str(parsed_plan.get("diagnosis_summary") or ""),
                parent_policy=str(parsed_plan.get("parent_policy") or ""),
            )
            for raw, idea_id in zip(list(parsed_plan.get("evolution_idea_records") or ()), registered_ideas, strict=False):
                if isinstance(raw, Mapping) and str(raw.get("reference") or "").strip():
                    idea_references[str(raw["reference"]).strip()] = idea_id
        # Every assigned Student needs a durable idea lineage, including the
        # heuristic/no-Teacher path. A matching Teacher idea is reused when
        # possible; otherwise the assigned claim becomes a recorded idea.
        hypotheses = [
            replace(
                hypothesis,
                epd_idea_id=(
                    self._matching_teacher_idea(
                        epd_database=epd_database,
                        idea_id=idea_references.get(hypothesis.teacher_idea_reference, ""),
                        hypothesis=hypothesis,
                    )
                    or epd_database.ensure_idea_for_hypothesis(
                        round_index=round_index,
                        parent=parent,
                        hypothesis=hypothesis,
                    )
                ),
            )
            if not hypothesis.epd_idea_id else hypothesis
            for hypothesis in hypotheses
        ]
        if teacher_plan_payload:
            teacher_plan_payload["hypotheses"] = [item.to_dict() for item in hypotheses]
            atomic_json(round_root / "teacher_plan.json", teacher_plan_payload)
        if not hypotheses:
            raise RuntimeError("planner returned no source-verified hypotheses")
        if len(hypotheses) > len(self.student_ids):
            raise RuntimeError("planner returned more hypotheses than available students")
        if len({item.novelty_key for item in hypotheses}) != len(hypotheses):
            raise RuntimeError("planner produced duplicate mechanism scopes in one round")
        assigned_student_ids = self.student_ids[: len(hypotheses)]
        print(f"[GoalEvolve][round={round_index:03d}][teacher] assigned students={len(hypotheses)} planner={self.planner.name}", flush=True)
        (round_root / "prompts").mkdir(exist_ok=True)
        teacher_prompt = round_root / "prompts" / "teacher.md"
        if not teacher_prompt.is_file():
            teacher_prompt.write_text(teacher_packet(contract=self.contract, parent=parent, round_index=round_index, retrieval_audit=audit, decision_context=decision_context), encoding="utf-8")
        prior = list((load_json(self.state_root / "knowledge" / "mechanism_registry.json", {"records": []}) or {}).get("records") or [])
        quarantine = load_json(self.state_root / "knowledge" / "evidence_quarantine.json", {}) or {}
        quarantined_hypotheses = {str(item) for item in list(quarantine.get("hypothesis_ids") or [])}
        prior = [
            row
            for row in prior
            if str(dict(row.get("hypothesis") or {}).get("hypothesis_id") or "") not in quarantined_hypotheses
        ]
        epd_portfolio = epd_database.role_portfolio(
            contract=self.contract,
            parent=parent,
        )
        atomic_json(round_root / "epd_role_portfolio.json", epd_portfolio)
        epd_records = list(epd_portfolio.get("records") or [])
        for student_id, hypothesis in zip(assigned_student_ids, hypotheses, strict=True):
            prompt = round_root / "prompts" / f"{student_id}.md"
            if not prompt.is_file():
                prompt.write_text(
                    student_packet(
                        parent=parent,
                        hypothesis=hypothesis,
                        prior=prior,
                        decision_context=decision_context,
                        epd_records=epd_records,
                    ),
                    encoding="utf-8",
                )
        prompt_paths = {student_id: round_root / "prompts" / f"{student_id}.md" for student_id in assigned_student_ids}
        candidates = self._recover_completed_candidates(
            round_root=round_root,
            parent=parent,
            hypotheses=hypotheses,
            student_ids=assigned_student_ids,
            round_index=round_index,
        ) if saved_teacher_plan is not None else []
        recovered_ids = {candidate.student_id for candidate in candidates}
        missing = [
            (student_id, hypothesis)
            for student_id, hypothesis in zip(
                assigned_student_ids, hypotheses, strict=True
            )
            if student_id not in recovered_ids
        ]
        if missing:
            pending_ids = [student_id for student_id, _ in missing]
            pending_hypotheses = [hypothesis for _, hypothesis in missing]
            candidates.extend(
                self._evaluate_parallel(
                    parent=parent,
                    hypotheses=pending_hypotheses,
                    student_ids=pending_ids,
                    prompt_paths=prompt_paths,
                    round_index=round_index,
                )
            )
        candidates.sort(key=lambda item: assigned_student_ids.index(item.student_id))
        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            # Every evaluator receives the same minimum source/diff provenance
            # check; the contest evaluator also applies its configured source
            # surface restrictions before this common gate.
            if "preflight" not in candidate.artifacts:
                report = preflight_candidate(candidate)
                candidate.artifacts["preflight"] = json.dumps(report.to_dict(), sort_keys=True)
                if not report.ok and not candidate.evaluation_error:
                    candidate.evaluation_error = ";".join(report.violations)
            stage_uses_recipe_baseline = str(decision_context.get("stage") or "") in {
                "timing_recovery",
                "adaptive_tradeoff",
            }
            power_recipe_baseline = (
                str(decision_context.get("stage") or "") == "power_reclaim"
                and candidate.hypothesis.timing_recipe_id == "rmp_area_power"
            )
            # A controller recipe is an intervention, so every completed
            # source experiment needs the same-source, same-recipe parent
            # measurement.  This includes a candidate that has already
            # regressed against lineage: EPD and the Teacher must distinguish
            # a bad source mechanism from a bad controller schedule.
            comparison_parent, recipe_baseline_status = self._recipe_matched_parent(
                parent=parent,
                hypothesis=candidate.hypothesis,
                decision_context=decision_context,
            )
            candidate.artifacts["matched_recipe_baseline"] = recipe_baseline_status
            verdict = self.promotion_policy.classify(contract=self.contract, parent=comparison_parent, candidate=candidate)
            if power_recipe_baseline and verdict.state in {"validated", "verified_qor_unattributed"}:
                lineage_verdict = self.promotion_policy.classify(
                    contract=self.contract,
                    parent=parent_at_start,
                    candidate=candidate,
                )
                if lineage_verdict.state not in {"validated", "verified_qor_unattributed"}:
                    verdict = EvidenceVerdict(
                        "refuted",
                        lineage_verdict.goal_distance,
                        lineage_verdict.distance_gain,
                        verdict.mechanism_fired,
                        verdict.integrity_ok and lineage_verdict.integrity_ok,
                        (*verdict.reasons, *lineage_verdict.reasons, "power_recipe_gain_without_lineage_gain"),
                    )
            # The matched baseline establishes causality for a recipe/source
            # experiment.  Promotion additionally requires that it improve
            # the common campaign parent; otherwise a different Tcl recipe
            # could replace the lineage with a worse absolute QoR.
            if (
                str(decision_context.get("stage") or "") in {"timing_recovery", "adaptive_tradeoff"}
                and verdict.state in {"validated", "verified_qor_unattributed"}
            ):
                # A recipe baseline has attribution authority only.  The
                # common lineage parent remains authoritative for absolute
                # gain, the active dominant residual, and preservation of
                # already-satisfied targets.  This prevents a planning-only
                # timing frontier from silently replacing the lineage
                # controller's promotion semantics.
                lineage_verdict = self.promotion_policy.classify(
                    contract=self.contract,
                    parent=parent_at_start,
                    candidate=candidate,
                )
                if lineage_verdict.state not in {"validated", "verified_qor_unattributed"}:
                    verdict = EvidenceVerdict(
                        "refuted",
                        lineage_verdict.goal_distance,
                        lineage_verdict.distance_gain,
                        verdict.mechanism_fired,
                        verdict.integrity_ok and lineage_verdict.integrity_ok,
                        (*verdict.reasons, *lineage_verdict.reasons, "matched_recipe_gain_without_lineage_admission"),
                    )
                elif recipe_baseline_status.startswith("unavailable:"):
                    # The source candidate may repair an engineering defect
                    # that makes the old source unable to execute this exact
                    # recipe.  Preserve a real lineage QoR gain, but do not
                    # overclaim source-only QoR attribution without a valid
                    # no-diff measurement.
                    verdict = EvidenceVerdict(
                        "verified_qor_unattributed",
                        lineage_verdict.goal_distance,
                        lineage_verdict.distance_gain,
                        verdict.mechanism_fired,
                        verdict.integrity_ok and lineage_verdict.integrity_ok,
                        (*verdict.reasons, "exact_recipe_baseline_unavailable", "lineage_admission_passed"),
                    )
            print(
                f"[GoalEvolve][round={round_index:03d}][student={candidate.student_id}] evidence={verdict.state} gain={verdict.distance_gain:.8f} mechanism_fired={str(verdict.mechanism_fired).lower()}",
                flush=True,
            )
            artifact = round_root / "students" / candidate.student_id / "artifacts"
            artifact.mkdir(parents=True, exist_ok=True)
            (artifact / "implementation.diff").write_text(candidate.implementation_diff, encoding="utf-8")
            atomic_json(artifact / "source_commit.json", {"source_commit": candidate.source_commit})
            atomic_json(artifact / "hypothesis.json", candidate.hypothesis.to_dict())
            atomic_json(artifact / "candidate.json", candidate.to_dict())
            atomic_json(artifact / "evidence.json", verdict.to_dict())
            preflight = candidate.artifacts.get("preflight")
            if preflight:
                try:
                    atomic_json(artifact / "preflight.json", json.loads(preflight))
                except (TypeError, ValueError):
                    (artifact / "preflight.txt").write_text(str(preflight), encoding="utf-8")
            execution = candidate.artifacts.get("execution")
            if execution:
                try:
                    atomic_json(artifact / "execution.json", json.loads(execution))
                except (TypeError, ValueError):
                    (artifact / "execution.txt").write_text(str(execution), encoding="utf-8")
            (artifact / "knowledge_card.md").write_text(
                "\n".join(
                    [
                        f"# {candidate.hypothesis.hypothesis_id}",
                        "",
                        f"- mechanism_family: `{candidate.hypothesis.mechanism_family}`",
                        f"- evidence_state: `{verdict.state}`",
                        f"- distance_gain: `{verdict.distance_gain:.8f}`",
                        f"- mechanism_fired: `{verdict.mechanism_fired}`",
                        f"- reasons: `{'; '.join(verdict.reasons)}`",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            rows.append({"candidate": candidate, "verdict": verdict, "comparison_parent": comparison_parent})
            epd_database.record(round_index=round_index, parent=comparison_parent, candidate=candidate, verdict=verdict)
        selected = self.promotion_policy.choose([(row["candidate"], row["verdict"]) for row in rows])
        if selected:
            candidate, verdict = selected
            parent = Parent(
                parent_id=f"round_{round_index:03d}:{candidate.student_id}",
                metrics=dict(candidate.metrics),
                source_commit=candidate.source_commit,
                source_hash=sha256_json({"parent": parent.source_hash, "diff": candidate.implementation_diff, "commit": candidate.source_commit}),
                # Parent.goal_distance always means the frozen three-metric
                # contract.  A staged power-only distance remains in the
                # evidence verdict and must not change this field's meaning.
                goal_distance=self.contract.evaluate(candidate.metrics)[0],
                evaluation_mode=candidate.hypothesis.evaluation_mode,
                timing_recipe_id=candidate.hypothesis.timing_recipe_id,
            )
            promoted = candidate.student_id
            promote_candidate = getattr(self.workspace_provider, "promote_candidate", None)
            if callable(promote_candidate):
                candidate_source = Path(candidate.artifacts["candidate_source"]) if candidate.artifacts.get("candidate_source") else None
                promote_candidate(state_root=self.state_root, parent=parent_at_start, candidate=parent, candidate_source=candidate_source, candidate_artifacts=candidate.artifacts)
            attempt_id = "EPD_" + sha256_json(
                {"source": candidate.source_commit, "hypothesis": candidate.hypothesis.hypothesis_id}
            )[:16]
            epd_database.mark_inherited(record_id=attempt_id, parent=parent)
            print(f"[GoalEvolve][round={round_index:03d}][teacher] promoted student={promoted} distance={parent.goal_distance:.8f}", flush=True)
        else:
            promoted = None
            print(f"[GoalEvolve][round={round_index:03d}][teacher] no_promotion parent_retained={parent.parent_id}", flush=True)
        integrated_card_ids = selected[0].hypothesis.retrieval_ids if selected else ()
        self._record_feedback(
            round_index=round_index,
            rows=rows,
            integrated_card_ids=integrated_card_ids,
        )
        self._record_timing_schedule_memory(round_index=round_index, rows=rows)
        teacher_review: dict[str, object] = {}
        if self.teacher is not None:
            teacher_review = self.teacher.review(
                state_root=self.state_root,
                round_root=round_root,
                round_index=round_index,
                parent=parent_at_start,
                parent_after=parent,
                promoted_student=promoted,
                decision_context=decision_context,
                diagnosis=round_diagnosis,
                rows=[(row["candidate"], row["verdict"]) for row in rows],
            )
            atomic_json(round_root / "teacher_review.json", teacher_review)
            review_markdown = str(teacher_review.get("teacher_markdown") or "")
            if review_markdown:
                (round_root / "teacher_review.md").write_text(review_markdown + "\n", encoding="utf-8")
            atomic_json(
                round_root / "teacher_review.parsed.json",
                dict(teacher_review.get("parsed_markdown") or {}),
            )
            self._apply_teacher_review_feedback(
                round_index=round_index,
                review=teacher_review,
                rows=rows,
            )
        commit_retrieval = getattr(self.planner, "commit_round", None)
        if callable(commit_retrieval):
            commit_retrieval(
                contract=self.contract,
                parent=parent_at_start,
                round_index=round_index,
                state_root=self.state_root,
                diagnosis=round_diagnosis,
                hypotheses=hypotheses,
                decision_context=decision_context,
            )
        token_usage = record_round_token_usage(state_root=self.state_root, round_root=round_root, round_index=round_index)
        token_totals = dict(token_usage["totals"])
        print(f"[GoalEvolve][round={round_index:03d}][tokens] calls={token_usage['call_count']} input={token_totals['input_tokens']} cached={token_totals['cached_input_tokens']} output={token_totals['output_tokens']} total={token_totals['total_tokens']}", flush=True)
        atomic_json(self.state_root / "parent.json", parent.to_dict())
        summary = {
            "schema_version": "goalevolve.v2.round.v1",
            "round": round_index,
            "common_parent_id_at_start": parent_at_start.parent_id,
            "common_parent_source_hash_at_start": parent_at_start.source_hash,
            "diagnosis": round_diagnosis.to_dict(),
            "decision_context": decision_context,
            "epd_summary": self._epd().summary(),
            "teacher_plan": teacher_plan_payload,
            "teacher_review": teacher_review,
            "token_usage": token_usage,
            "student_count": len(rows),
            "promoted_student": promoted,
            "parent_after": parent.to_dict(),
            "results": [{"student_id": row["candidate"].student_id, "hypothesis_id": row["candidate"].hypothesis.hypothesis_id, "timing_recipe_id": row["candidate"].hypothesis.timing_recipe_id, "comparison_parent": row["comparison_parent"].to_dict(), "verdict": row["verdict"].to_dict()} for row in rows],
        }
        atomic_json(round_root / "round.json", summary)
        (round_root / "review.md").write_text(review_packet(verdicts=[row["verdict"] for row in rows]), encoding="utf-8")
        try:
            leaderboard = update_unified_leaderboard(
                leaderboard_root=self.state_root.parent / "leaderboard",
                state_roots=(self.state_root,),
                top_k=5,
            )
            design_rows = [
                row for row in leaderboard["rows"]
                if row["design"] == self.contract.design
                and row["contract_id"] == self.contract.contract_id
            ]
            if design_rows:
                best = design_rows[0]
                print(
                    f"[GoalEvolve][leaderboard] design={self.contract.design} "
                    f"entries={len(design_rows)} best={best['result_id']} "
                    f"distance={float(best['goal_distance']):.8f}",
                    flush=True,
                )
            else:
                print(
                    f"[GoalEvolve][leaderboard] design={self.contract.design} entries=0 best=none",
                    flush=True,
                )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Reporting must never invalidate an already committed round.
            print(f"[GoalEvolve][leaderboard] update_failed={type(exc).__name__}:{exc}", flush=True)
        return parent

    def _adaptive_frontier_context(
        self,
        *,
        parent: Parent,
        decision_context: dict[str, object],
    ) -> dict[str, object]:
        """Diagnose adaptive work at the best exact same-source recipe point.

        A timing recipe can expose a much closer operating point without any
        source-diff gain.  Such a result must never replace the lineage
        parent, but continuing to allocate Students from the generic stage
        metrics can keep diagnosing TNS after the recipe already satisfies
        timing and makes power the real downstream bottleneck.  Only cached
        no-diff comparison parents with the current source hash are eligible
        planning anchors; runtime and observer scores are ignored.
        """
        if str(decision_context.get("stage") or "") != "adaptive_tradeoff":
            return decision_context
        payload = load_json(
            self.state_root / "knowledge" / "timing_schedule_memory.json",
            {"records": []},
        ) or {"records": []}
        frontier_rows: list[tuple[float, dict[str, object], str, int]] = []
        for raw in list(dict(payload).get("records") or []):
            record = dict(raw) if isinstance(raw, Mapping) else {}
            comparison = dict(record.get("comparison_parent") or {})
            if str(comparison.get("source_hash") or "") != parent.source_hash:
                continue
            if str(comparison.get("evaluation_mode") or "") != "power_then_timing":
                continue
            metrics = dict(comparison.get("metrics") or {})
            try:
                if float(metrics.get("drv_count", float("inf"))) != 0.0:
                    continue
            except (TypeError, ValueError):
                continue
            distance, residuals, missing = self.contract.evaluate(metrics)
            if missing:
                continue
            frontier_rows.append(
                (
                    float(distance),
                    {key: value for key, value in metrics.items() if key != "runtime_s"},
                    str(record.get("recipe_id") or ""),
                    int(record.get("round") or 0),
                )
            )
        if not frontier_rows:
            return decision_context
        distance, metrics, recipe_id, source_round = min(frontier_rows, key=lambda row: row[0])
        lineage_distance, lineage_residuals, _ = self.contract.evaluate(parent.metrics)
        if distance >= lineage_distance - 1e-12:
            return decision_context
        _, residuals, _ = self.contract.evaluate(metrics)
        unresolved = {
            name: float(value)
            for name, value in residuals.items()
            if value is not None and float(value) > 0.0
        }
        dominant = max(unresolved, key=unresolved.get) if unresolved else ""
        updated = dict(decision_context)
        updated["lineage_parent_normalized_residuals"] = lineage_residuals
        updated["normalized_residuals"] = residuals
        updated["dominant_metric"] = dominant
        updated["primary_metrics"] = [dominant] if dominant else []
        updated["diagnostic_frontier"] = {
            "role": "planning_only_exact_recipe_baseline",
            "source_hash": parent.source_hash,
            "source_round": source_round,
            "recipe_id": recipe_id,
            "metrics": metrics,
            "goal_distance": distance,
            "lineage_parent_goal_distance": lineage_distance,
            "promotion_authority": False,
        }
        return updated

    @staticmethod
    def _incomplete_teacher_plan(
        round_root: Path,
    ) -> tuple[list[Hypothesis], dict[str, object]] | None:
        """Load the immutable allocation for an interrupted, uncommitted round.

        The Teacher plan is written before any Student workspace is edited.
        It is therefore the authoritative schema for interpreting completed
        artifacts after a controller crash.  A committed ``round.json`` always
        wins and disables this path.
        """
        path = round_root / "teacher_plan.json"
        if (round_root / "round.json").is_file() or not path.is_file():
            return None
        payload = load_json(path, {}) or {}
        if not isinstance(payload, dict):
            raise RuntimeError(f"incomplete_round_teacher_plan_invalid:{path}")
        hypotheses: list[Hypothesis] = []
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
        for raw in list(payload.get("hypotheses") or []):
            if not isinstance(raw, Mapping):
                continue
            normalized = dict(raw)
            for field in tuple_fields:
                if field in normalized:
                    normalized[field] = tuple(normalized[field] or ())
            if "candidate_options" in normalized:
                normalized["candidate_options"] = tuple(
                    dict(item) for item in normalized["candidate_options"] or () if isinstance(item, Mapping)
                )
            try:
                hypotheses.append(Hypothesis(**normalized))
            except TypeError as exc:
                raise RuntimeError(
                    f"incomplete_round_hypothesis_invalid:{path}:{exc}"
                ) from exc
        if not hypotheses:
            raise RuntimeError(f"incomplete_round_has_no_hypotheses:{path}")
        return hypotheses, payload

    def _recover_completed_candidates(
        self,
        *,
        round_root: Path,
        parent: Parent,
        hypotheses: Sequence[Hypothesis],
        student_ids: Sequence[str],
        round_index: int,
    ) -> list[CandidateResult]:
        """Recover terminal official-flow artifacts without rerunning them.

        Recovery is deliberately evaluator-owned: only the evaluator knows
        which files and terminal markers prove that its build, flow, metric,
        placement, and official LEC stages actually finished.  Missing or
        partial work is left for the normal Student path; a recovered Student
        is never edited, rebuilt, or remeasured.
        """
        recover = getattr(self.evaluator, "recover_completed_candidate", None)
        if not callable(recover):
            return []
        recovered: list[CandidateResult] = []
        for student_id, hypothesis in zip(student_ids, hypotheses, strict=True):
            workspace = round_root / "students" / student_id / "workspace"
            manifest_path = workspace.parent / "workspace_manifest.json"
            manifest = load_json(manifest_path, {}) or {}
            if not workspace.is_dir() or not isinstance(manifest, Mapping):
                continue
            if (
                str(manifest.get("parent_id") or "") != parent.parent_id
                or str(manifest.get("parent_source_hash") or "") != parent.source_hash
            ):
                raise RuntimeError(
                    f"incomplete_round_parent_mismatch:{manifest_path}"
                )
            candidate = recover(
                contract=self.contract,
                parent=parent,
                hypothesis=hypothesis,
                student_id=student_id,
                workspace=workspace,
                round_index=round_index,
            )
            if candidate is None:
                continue
            candidate.artifacts["recovery"] = "completed_artifacts_reused_without_execution"
            recovered.append(candidate)
            print(
                f"[GoalEvolve][round={round_index:03d}][student={student_id}] "
                "recovered_completed_flow=true rerun=false",
                flush=True,
            )
        return recovered

    def _evaluate_parallel(self, *, parent: Parent, hypotheses, student_ids, prompt_paths: dict[str, Path], round_index: int) -> list[CandidateResult]:
        jobs = {}
        results: list[CandidateResult] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(student_ids))) as pool:
            for student_id, hypothesis in zip(student_ids, hypotheses, strict=True):
                workspace = self.workspace_provider.prepare(state_root=self.state_root, round_index=round_index, student_id=student_id, parent=parent)
                print(f"[GoalEvolve][round={round_index:03d}][student={student_id}] queued hypothesis={hypothesis.hypothesis_id}", flush=True)
                future = pool.submit(self._edit_then_evaluate, parent, hypothesis, student_id, workspace, prompt_paths[student_id], round_index)
                jobs[future] = (student_id, hypothesis)
            for future in as_completed(jobs):
                student_id, hypothesis = jobs[future]
                try:
                    results.append(future.result())
                    print(f"[GoalEvolve][round={round_index:03d}][student={student_id}] evaluation_complete", flush=True)
                except Exception as exc:  # A student failure becomes evidence, never kills the whole round.
                    results.append(CandidateResult(student_id, hypothesis, {}, {}, [], "", "", evaluation_error=str(exc)))
                    print(f"[GoalEvolve][round={round_index:03d}][student={student_id}] evaluation_exception={type(exc).__name__}", flush=True)
        return sorted(results, key=lambda item: student_ids.index(item.student_id))

    def _edit_then_evaluate(self, parent: Parent, hypothesis, student_id: str, workspace: Path, prompt_path: Path, round_index: int) -> CandidateResult:
        if self.student_editor is None:
            candidate = self.evaluator.evaluate(contract=self.contract, parent=parent, hypothesis=hypothesis, student_id=student_id, workspace=workspace, round_index=round_index)
            candidate.artifacts["codex"] = json.dumps({"ok": True, "detail": "editor_disabled"}, sort_keys=True)
            return candidate
        edit = self.student_editor.apply(state_root=self.state_root, round_index=round_index, student_id=student_id, workspace=workspace, parent=parent, hypothesis=hypothesis, prompt_path=prompt_path)
        if not edit.ok:
            return CandidateResult(
                student_id,
                hypothesis,
                {},
                {},
                [],
                "",
                "",
                artifacts={"codex": json.dumps(edit.to_dict(), sort_keys=True), **edit.artifacts},
                evaluation_error=edit.detail,
            )
        candidate = self.evaluator.evaluate(
            contract=self.contract,
            parent=parent,
            hypothesis=hypothesis,
            student_id=student_id,
            workspace=workspace,
            round_index=round_index,
        )
        codex_reports: dict[str, object] = {"initial": edit.to_dict(), "repairs": []}
        candidate.artifacts["codex"] = json.dumps(codex_reports, sort_keys=True)
        candidate.artifacts.update(edit.artifacts)
        max_repairs = int(getattr(getattr(self.student_editor, "config", None), "max_repair_attempts", 0))
        repair_artifacts: dict[str, str] = {}
        for repair_attempt in range(1, max_repairs + 1):
            if not self._repairable_engineering_failure(candidate):
                break
            failure_context = self._failure_context(candidate=candidate, workspace=workspace)
            self._snapshot_failed_evaluation(candidate=candidate, workspace=workspace, repair_attempt=repair_attempt)
            print(
                f"[GoalEvolve][round={round_index:03d}][student={student_id}] engineering_failure repair={repair_attempt}/{max_repairs}",
                flush=True,
            )
            repair = self.student_editor.repair(
                state_root=self.state_root,
                round_index=round_index,
                student_id=student_id,
                workspace=workspace,
                parent=parent,
                hypothesis=hypothesis,
                prompt_path=prompt_path,
                failure_context=failure_context,
                repair_attempt=repair_attempt,
            )
            cast_repairs = codex_reports["repairs"]
            assert isinstance(cast_repairs, list)
            cast_repairs.append(repair.to_dict())
            if not repair.ok:
                candidate.artifacts["codex"] = json.dumps(codex_reports, sort_keys=True)
                repair_artifacts.update({f"repair_{repair_attempt:02d}_{key}": value for key, value in repair.artifacts.items()})
                candidate.artifacts.update(repair_artifacts)
                candidate.evaluation_error = f"{candidate.evaluation_error or 'engineering_failure'};student_repair_failed:{repair.detail}"
                break
            candidate = self.evaluator.evaluate(
                contract=self.contract,
                parent=parent,
                hypothesis=hypothesis,
                student_id=student_id,
                workspace=workspace,
                round_index=round_index,
            )
            candidate.artifacts["codex"] = json.dumps(codex_reports, sort_keys=True)
            candidate.artifacts.update(edit.artifacts)
            repair_artifacts.update({f"repair_{repair_attempt:02d}_{key}": value for key, value in repair.artifacts.items()})
            candidate.artifacts.update(repair_artifacts)
        # A build/flow can complete while leaving an electrical violation.
        # This is not terminal evidence for a power hypothesis: return the
        # concrete DRV result to the same author once, so it can repair the
        # power-path implementation in the same round rather than wasting the
        # next evolutionary choice on a known incomplete candidate.
        if self._repairable_constraint_failure(candidate):
            constraint_context = self._constraint_failure_context(candidate=candidate, workspace=workspace)
            self._snapshot_failed_evaluation(candidate=candidate, workspace=workspace, repair_attempt=1, repair_kind="constraint")
            print(
                f"[GoalEvolve][round={round_index:03d}][student={student_id}] electrical_constraint repair=1/1",
                flush=True,
            )
            constraint_repair = self.student_editor.repair(
                state_root=self.state_root,
                round_index=round_index,
                student_id=student_id,
                workspace=workspace,
                parent=parent,
                hypothesis=hypothesis,
                prompt_path=prompt_path,
                failure_context=constraint_context,
                repair_attempt=1,
                repair_kind="electrical_constraint",
            )
            cast_repairs = codex_reports["repairs"]
            assert isinstance(cast_repairs, list)
            cast_repairs.append(constraint_repair.to_dict())
            if constraint_repair.ok:
                candidate = self.evaluator.evaluate(
                    contract=self.contract,
                    parent=parent,
                    hypothesis=hypothesis,
                    student_id=student_id,
                    workspace=workspace,
                    round_index=round_index,
                )
                candidate.artifacts["codex"] = json.dumps(codex_reports, sort_keys=True)
                candidate.artifacts.update(edit.artifacts)
                candidate.artifacts.update(
                    {f"constraint_repair_{key}": value for key, value in constraint_repair.artifacts.items()}
                )
            else:
                candidate.artifacts["codex"] = json.dumps(codex_reports, sort_keys=True)
                candidate.evaluation_error = f"{candidate.evaluation_error or 'electrical_constraint'};student_repair_failed:{constraint_repair.detail}"
        # Missing expected telemetry is an incomplete experiment even when
        # the first run has no QoR gain.  Give the same Student one bounded
        # opportunity to repair the real activation/instrumentation boundary
        # and immediately re-run the full evaluator.  A card-specific
        # conclusive nonactivation signal is the only exception.  For a QoR
        # winner, preserve the original source/result so instrumentation can
        # never discard a verified better candidate.
        provisional = self.promotion_policy.classify(
            contract=self.contract, parent=parent, candidate=candidate
        )
        telemetry_incomplete = (
            not provisional.mechanism_fired
            and provisional.integrity_ok
            and not self._has_conclusive_nonactivation(candidate)
        )
        if provisional.state == "verified_qor_unattributed" or telemetry_incomplete:
            # Instrumentation itself is a source edit.  Preserve the exact
            # tree and CandidateResult that produced the verified QoR before
            # asking the same Student to touch it.  A measurement-neutral
            # telemetry patch must never accidentally discard a better parent
            # simply because it perturbs a marginal result.
            original_candidate = deepcopy(candidate)
            original_source = workspace.parent / "artifacts" / "telemetry_repair" / "source_before_repair"
            try:
                clone_source_tree(workspace / "source", original_source)
            except (OSError, RuntimeError) as exc:
                original_source = None
                original_candidate.artifacts["telemetry_repair_snapshot_error"] = str(exc)
            if original_source is not None:
                original_candidate.artifacts["candidate_source"] = str(original_source)
                original_candidate.artifacts["telemetry_repair_source_before"] = str(original_source)
            telemetry_attempt = 1
            telemetry_context = self._telemetry_repair_context(
                candidate=candidate, verdict=provisional, workspace=workspace
            )
            self._snapshot_failed_evaluation(
                candidate=candidate,
                workspace=workspace,
                repair_attempt=telemetry_attempt,
                repair_kind="telemetry",
            )
            print(
                f"[GoalEvolve][round={round_index:03d}][student={student_id}] "
                "missing_telemetry repair=1/1",
                flush=True,
            )
            telemetry_repair = self.student_editor.repair(
                state_root=self.state_root,
                round_index=round_index,
                student_id=student_id,
                workspace=workspace,
                parent=parent,
                hypothesis=hypothesis,
                prompt_path=prompt_path,
                failure_context=telemetry_context,
                repair_attempt=telemetry_attempt,
                repair_kind="telemetry",
            )
            cast_repairs = codex_reports["repairs"]
            assert isinstance(cast_repairs, list)
            cast_repairs.append(telemetry_repair.to_dict())
            original_candidate.artifacts["codex"] = json.dumps(codex_reports, sort_keys=True)
            candidate.artifacts["codex"] = json.dumps(codex_reports, sort_keys=True)
            candidate.artifacts.update(
                {f"telemetry_repair_{key}": value for key, value in telemetry_repair.artifacts.items()}
            )
            if telemetry_repair.ok:
                repaired_candidate = self.evaluator.evaluate(
                    contract=self.contract,
                    parent=parent,
                    hypothesis=hypothesis,
                    student_id=student_id,
                    workspace=workspace,
                    round_index=round_index,
                )
                repaired_candidate.artifacts["codex"] = json.dumps(codex_reports, sort_keys=True)
                repaired_candidate.artifacts.update(edit.artifacts)
                repaired_candidate.artifacts.update(
                    {f"telemetry_repair_{key}": value for key, value in telemetry_repair.artifacts.items()}
                )
                repair_result = workspace.parent / "artifacts" / "telemetry_repair" / "candidate_after_repair.json"
                atomic_json(repair_result, repaired_candidate.to_dict())
                original_candidate.artifacts["telemetry_repair_candidate"] = str(repair_result)
                original_candidate.artifacts.update(
                    {f"telemetry_repair_{key}": value for key, value in telemetry_repair.artifacts.items()}
                )
                repaired_verdict = self.promotion_policy.classify(
                    contract=self.contract, parent=parent, candidate=repaired_candidate
                )
                # A previously unattributed QoR winner keeps its original
                # result unless the instrumented run is at least as good.
                # For a no-gain activation repair, retain the rerun itself so
                # EPD/Teacher see whether the mechanism actually fired.
                if provisional.state != "verified_qor_unattributed":
                    candidate = repaired_candidate
                elif (
                    repaired_verdict.state in {"validated", "verified_qor_unattributed"}
                    and repaired_verdict.goal_distance <= provisional.goal_distance
                ):
                    candidate = repaired_candidate
                else:
                    outcome = "repaired_evaluation_worse" if repaired_verdict.goal_distance > provisional.goal_distance else f"repaired_evaluation_{repaired_verdict.state}"
                    original_candidate.artifacts["telemetry_repair_outcome"] = f"{outcome};retained_original_verified_qor"
                    candidate = original_candidate
            else:
                candidate = original_candidate
        return candidate

    @staticmethod
    def _repairable_engineering_failure(candidate: CandidateResult) -> bool:
        """Whether a failed evaluation is meaningful for same-Student repair.

        Resource exhaustion and an absent edit cannot be fixed by altering C++.
        Configure/build/flow/check failures, by contrast, are returned to the
        author before EPD assigns a terminal program status.
        """
        error = (candidate.evaluation_error or "").lower()
        if not error or "codex_failed" in error or "no_source_change" in error:
            return False
        # A SIGSEGV (11) is an actionable source-level regression.  Do not
        # group it with timeout/OOM/operator termination: the same Student
        # receives the stack trace and must repair its C++ change this round.
        if "terminated_signal:11" in error or "signal 11" in error:
            return True
        if any(token in error for token in ("insufficient_disk", "timeout", "terminated_signal", "permission", "missing benchmark", "missing asap7")):
            return False
        return any(token in error for token in ("configure", "build", "flow", "metrics", "official_4of4", "filenotfound", "runtimeerror"))

    @staticmethod
    def _repairable_constraint_failure(candidate: CandidateResult) -> bool:
        if candidate.evaluation_error:
            return False
        checks = {item.name: item.passed for item in candidate.checks}
        if not all(checks.get(name, False) for name in ("build", "flow", "metrics", "lec")):
            return False
        try:
            return float(candidate.metrics.get("drv_count", 0.0)) != 0.0
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _constraint_failure_context(*, candidate: CandidateResult, workspace: Path) -> str:
        drv = candidate.metrics.get("drv_count")
        lines = [
            "evaluation_status: build, official post-route flow, metrics, and official 4/4 LEC completed",
            f"hard_constraint_failure: drv_count={drv}; a promotable candidate requires drv_count=0",
            "repair_goal: preserve the assigned repair_power mechanism and its power direction, but restore every introduced slew/cap/fanout violation through the power-path policy's own journal/acceptance logic",
            f"workspace: {workspace}",
        ]
        for key in ("evaluation_log", "metrics_csv", "official_4of4_log"):
            if candidate.artifacts.get(key):
                lines.append(f"{key}: {candidate.artifacts[key]}")
        return "\n".join(lines)

    @staticmethod
    def _telemetry_repair_context(*, candidate: CandidateResult, verdict, workspace: Path) -> str:
        expected = ", ".join(candidate.hypothesis.expected_signals) or "<none>"
        observed = json.dumps(candidate.phase_signals, sort_keys=True)
        lines = [
                "evaluation_status: complete official build/flow/metrics/LEC passed; expected mechanism telemetry is incomplete",
                f"workspace: {workspace}",
                f"expected_signals: {expected}",
                f"observed_signals: {observed}",
                f"provisional_evidence_state: {verdict.state}",
                "Required repair: add minimal runtime METRIC lines at the real executed mechanism boundary. "
                "For every expected signal, emit a nonzero value only when the corresponding event occurs; "
                "the event name is semantic: never derive a deep-window signal from an earlier prefix/tranche, "
                "a rejected alternative, a loop boundary, or a proxy count. If the named event did not execute, "
                "leave its signal absent (or zero) and report the genuine nonactivation; do not fabricate activation "
                "or change the optimization policy merely to print telemetry. "
                "The controller will rebuild and execute the full official post-route flow immediately after this repair.",
        ]
        # The editor operates from its private source tree, whereas official
        # artifacts live beside it.  Give it the exact evaluated log and a
        # bounded tail so it diagnoses the executed branch rather than
        # guessing from source alone.
        for label in ("evaluation_log", "checkpoint_metrics", "official_4of4_log"):
            value = candidate.artifacts.get(label)
            if not value:
                continue
            path = Path(value)
            lines.append(f"## {label}: {path}")
            try:
                lines.append(path.read_text(encoding="utf-8", errors="ignore")[-12000:])
            except OSError:
                lines.append("<unreadable>")
        return "\n".join(lines)

    @staticmethod
    def _failure_context(*, candidate: CandidateResult, workspace: Path) -> str:
        lines = [f"evaluation_error: {candidate.evaluation_error or '<none>'}", f"workspace: {workspace}"]
        if candidate.implementation_diff:
            lines.extend(["## Current source diff", candidate.implementation_diff[-12000:]])
        for label, value in sorted(candidate.artifacts.items()):
            if not label.endswith("log") and label not in {"evaluation_log", "build_log", "configure_log"}:
                continue
            path = Path(value)
            lines.append(f"## {label}: {path}")
            try:
                lines.append(path.read_text(encoding="utf-8", errors="ignore")[-8000:])
            except OSError:
                lines.append("<log unavailable>")
        return "\n".join(lines)

    @staticmethod
    def _snapshot_failed_evaluation(
        *,
        candidate: CandidateResult,
        workspace: Path,
        repair_attempt: int,
        repair_kind: str = "engineering",
    ) -> None:
        """Retain controller evidence even though the next evaluation reuses paths."""
        destination = workspace.parent / "artifacts" / "repair_attempts" / repair_kind / f"attempt_{repair_attempt:02d}" / "evaluation"
        destination.mkdir(parents=True, exist_ok=True)
        retained: dict[str, str] = {}
        for label, value in candidate.artifacts.items():
            # ``artifacts`` deliberately carries both paths and structured
            # provenance such as the serialized Codex turn report.  Only the
            # bounded set of retained evidence keys is a path.  Constructing
            # a Path for a large JSON report can itself raise ENAMETOOLONG and
            # must never abort the same-Student repair loop.
            if not (label.endswith("log") or label in {"evaluation_log", "build_log", "configure_log", "checkpoint_metrics"}):
                continue
            if not isinstance(value, str):
                continue
            try:
                path = Path(value)
                is_file = path.is_file()
            except OSError:
                continue
            if is_file:
                target = destination / f"{label}{path.suffix}"
                shutil.copy2(path, target)
                retained[label] = str(target)
        atomic_json(destination / "candidate_before_repair.json", candidate.to_dict())
        atomic_json(destination / "retained_artifacts.json", retained)

    def _record_feedback(
        self,
        *,
        round_index: int,
        rows: Sequence[dict[str, Any]],
        integrated_card_ids: Sequence[str] = (),
    ) -> None:
        registry_path = self.state_root / "knowledge" / "mechanism_registry.json"
        registry = load_json(registry_path, {"schema_version": "goalevolve.v2.mer.v1", "records": []}) or {"records": []}
        feedback_path = self.state_root / "knowledge" / "feedback.json"
        feedback = load_json(feedback_path, {"suppressed_card_ids": []}) or {"suppressed_card_ids": []}
        suppressed = set(feedback.get("suppressed_card_ids") or [])
        integrated = set(feedback.get("integrated_card_ids") or [])
        activation_retry = set(feedback.get("activation_retry_card_ids") or [])
        # A missing signal is not outcome evidence, but it must not turn into
        # an infinite high-priority loop.  One retry is enough to correct a
        # controller/telemetry wiring defect; a second completed nonactivation
        # establishes that this hook is currently unreachable for the fixed
        # recipe and must release the Student slot to a different mechanism.
        # Older campaigns have retry IDs but no counter: treat that persisted
        # retry state as one prior attempt for backward-compatible migration.
        activation_attempts = {
            str(card_id): int(count)
            for card_id, count in dict(feedback.get("activation_retry_attempts") or {}).items()
        }
        for row in rows:
            candidate: CandidateResult = row["candidate"]
            verdict = row["verdict"]
            registry.setdefault("records", []).append({"round": round_index, "hypothesis": candidate.hypothesis.to_dict(), "state": verdict.state, "distance_gain": verdict.distance_gain, "reasons": list(verdict.reasons)})
            ObservationMemory(self.state_root).record(round_index=round_index, candidate=candidate, verdict=verdict)
            # A completed QoR regression only refutes a mechanism after the
            # mechanism actually fired.  Missing expected telemetry means an
            # activation/configuration defect (for example a recipe feature
            # flag absent from Tcl), not negative evidence about the C++
            # hook.  Keep that card admissible for one corrected execution;
            # otherwise the retriever would permanently hide an experiment
            # that never ran.
            if verdict.state == "refuted" and verdict.mechanism_fired:
                suppressed.update(candidate.hypothesis.retrieval_ids)
            if verdict.state == "refuted" and not verdict.mechanism_fired and self._has_conclusive_nonactivation(candidate):
                # The source itself reports that the fixed recipe exhausted
                # this card's admission boundary.  Unlike an absent telemetry
                # line, this cannot be repaired by replaying the same card.
                suppressed.update(candidate.hypothesis.retrieval_ids)
                activation_retry.difference_update(candidate.hypothesis.retrieval_ids)
                for card_id in candidate.hypothesis.retrieval_ids:
                    activation_attempts.pop(card_id, None)
            elif verdict.state == "refuted" and not verdict.mechanism_fired:
                for card_id in candidate.hypothesis.retrieval_ids:
                    prior_attempts = activation_attempts.get(
                        card_id, 1 if card_id in activation_retry else 0
                    )
                    attempts = prior_attempts + 1
                    activation_attempts[card_id] = attempts
                    if attempts >= 2:
                        suppressed.add(card_id)
                        activation_retry.discard(card_id)
                    else:
                        activation_retry.add(card_id)
            elif verdict.mechanism_fired:
                activation_retry.difference_update(candidate.hypothesis.retrieval_ids)
                for card_id in candidate.hypothesis.retrieval_ids:
                    activation_attempts.pop(card_id, None)
        feedback["suppressed_card_ids"] = sorted(suppressed)
        feedback["activation_retry_card_ids"] = sorted(activation_retry)
        feedback["activation_retry_attempts"] = dict(sorted(activation_attempts.items()))
        # Only the selected winner became the next common source parent.
        # Validated siblings remain independently testable.
        integrated.update(str(card_id) for card_id in integrated_card_ids)
        feedback["integrated_card_ids"] = sorted(integrated)
        atomic_json(registry_path, registry)
        atomic_json(feedback_path, feedback)

    @staticmethod
    def _has_conclusive_nonactivation(candidate: CandidateResult) -> bool:
        """Return true only for a card-specific completed-log disproof.

        A generic missing metric still deserves one telemetry/activation
        repair.  A card may, however, name a source-level log event which
        proves that its fixed admission condition had no eligible action.
        """
        patterns = tuple(candidate.hypothesis.conclusive_nonactivation_patterns)
        if not patterns:
            return False
        raw_path = candidate.artifacts.get("evaluation_log")
        if not raw_path:
            return False
        try:
            log_text = Path(raw_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return all(pattern in log_text for pattern in patterns)

    def _apply_teacher_review_feedback(
        self,
        *,
        round_index: int,
        review: Mapping[str, object],
        rows: Sequence[dict[str, Any]],
    ) -> None:
        """Make an evidence-backed Teacher suppression binding for retrieval.

        The retriever deliberately permits one missing-telemetry retry.  A
        completed Teacher review can nevertheless establish that a family has
        exhausted that repair path across prior official runs.  Previously that
        conclusion lived only in prose, so a later fallback could reassign the
        same Student slot.  Translate only explicit, evidence-classified
        ``suppress`` actions for hypotheses actually evaluated in this round;
        this never grants the Teacher authority to suppress unrelated cards.
        """
        actions = review.get("mechanism_actions")
        if not isinstance(actions, list):
            return
        conclusive = {
            "fully_evaluated_qor_refutation",
            "activation_failure_engineering_exhausted",
        }
        suppressed_families = {
            str(action.get("mechanism_family") or "")
            for action in actions
            if isinstance(action, Mapping)
            and str(action.get("action") or "").strip().lower() == "suppress"
            and str(action.get("evidence_classification") or "") in conclusive
        }
        if not suppressed_families:
            return

        feedback_path = self.state_root / "knowledge" / "feedback.json"
        feedback = load_json(feedback_path, {"suppressed_card_ids": []}) or {}
        suppressed = set(feedback.get("suppressed_card_ids") or [])
        activation_retry = set(feedback.get("activation_retry_card_ids") or [])
        attempts = {
            str(card_id): int(count)
            for card_id, count in dict(feedback.get("activation_retry_attempts") or {}).items()
        }
        applied: list[dict[str, object]] = []
        for row in rows:
            candidate: CandidateResult = row["candidate"]
            if candidate.hypothesis.mechanism_family not in suppressed_families:
                continue
            card_ids = set(candidate.hypothesis.retrieval_ids)
            if not card_ids:
                continue
            suppressed.update(card_ids)
            activation_retry.difference_update(card_ids)
            for card_id in card_ids:
                attempts.pop(card_id, None)
            applied.append(
                {
                    "round": round_index,
                    "mechanism_family": candidate.hypothesis.mechanism_family,
                    "hypothesis_id": candidate.hypothesis.hypothesis_id,
                    "card_ids": sorted(card_ids),
                }
            )
        if not applied:
            return
        feedback["suppressed_card_ids"] = sorted(suppressed)
        feedback["activation_retry_card_ids"] = sorted(activation_retry)
        feedback["activation_retry_attempts"] = dict(sorted(attempts.items()))
        history = list(feedback.get("teacher_suppression_history") or [])
        history.extend(applied)
        feedback["teacher_suppression_history"] = history[-100:]
        atomic_json(feedback_path, feedback)

    def _load_parent(self) -> Parent:
        data = load_json(self.state_root / "parent.json")
        if not isinstance(data, dict):
            raise RuntimeError("parent.json is missing or invalid")
        return Parent(**data)

    def _adopt_execution_champion(self, parent: Parent) -> Parent:
        """Adopt the closest complete source+recipe operating point.

        Mechanism attribution and executable search state are different
        questions.  A Student source edit can be neutral against its matched
        no-diff recipe baseline while the resulting source+recipe combination
        is still much closer to the frozen target than the current lineage.
        When explicitly enabled, keep that evidence classification intact but
        use the better fully checked operating point as the next search parent.
        Runtime and observer scores are never read here.
        """
        if not self.prefer_execution_champion:
            return parent
        eligible: list[tuple[float, float, float, float, Path, dict[str, object]]] = []
        rounds_root = self.state_root / "rounds"
        for path in rounds_root.glob("round_*/students/*/artifacts/candidate.json"):
            round_root = path.parents[3]
            if not (round_root / "round.json").is_file():
                continue
            candidate = load_json(path, {}) or {}
            if not isinstance(candidate, Mapping) or candidate.get("evaluation_error"):
                continue
            checks = {
                str(item.get("name") or ""): bool(item.get("passed"))
                for item in list(candidate.get("checks") or [])
                if isinstance(item, Mapping)
            }
            if not all(checks.get(name, False) for name in ("build", "flow", "metrics", "lec")):
                continue
            metrics = dict(candidate.get("metrics") or {})
            try:
                drv = float(metrics.get("drv_count", float("inf")))
                tns = float(metrics.get("tns_abs_ns", float("inf")))
                dynamic = float(metrics.get("dynamic_power_pw", float("inf")))
                leakage = float(metrics.get("leakage_power_pw", float("inf")))
            except (TypeError, ValueError):
                continue
            if drv != 0.0:
                continue
            distance, _, missing = self.contract.evaluate(metrics)
            source = path.parents[1] / "workspace" / "source"
            if missing or not source.is_dir():
                continue
            eligible.append((distance, tns, leakage, dynamic, path, dict(candidate)))
        if not eligible:
            return parent
        distance, _, _, _, candidate_path, candidate = min(eligible, key=lambda row: row[:4])
        if distance >= parent.goal_distance - 1e-12:
            return parent

        hypothesis = dict(candidate.get("hypothesis") or {})
        metrics = {
            str(name): float(value)
            for name, value in dict(candidate.get("metrics") or {}).items()
            if isinstance(value, (int, float))
        }
        student_root = candidate_path.parents[1]
        manifest = load_json(student_root / "workspace_manifest.json", {}) or {}
        parent_source_hash = str(manifest.get("parent_source_hash") or "")
        implementation_diff = str(candidate.get("implementation_diff") or "")
        source_commit = str(candidate.get("source_commit") or "")
        if not parent_source_hash or not implementation_diff or not source_commit:
            return parent
        source_hash = sha256_json(
            {
                "parent": parent_source_hash,
                "diff": implementation_diff,
                "commit": source_commit,
            }
        )
        round_index = int(candidate_path.parents[3].name.rsplit("_", 1)[-1])
        student_id = student_root.name
        champion = Parent(
            parent_id=f"round_{round_index:03d}:{student_id}",
            metrics=metrics,
            source_commit=source_commit,
            source_hash=source_hash,
            goal_distance=distance,
            evaluation_mode=str(hypothesis.get("evaluation_mode") or "unknown"),
            timing_recipe_id=str(hypothesis.get("timing_recipe_id") or "legacy_setup"),
        )
        artifacts = {
            str(name): str(value)
            for name, value in dict(candidate.get("artifacts") or {}).items()
        }
        artifacts.setdefault(
            "evaluation_tcl",
            str(candidate_path.parent / "contest_output" / "evaluate.tcl"),
        )
        promote_candidate = getattr(self.workspace_provider, "promote_candidate", None)
        if not callable(promote_candidate):
            raise RuntimeError("execution_champion_requires_workspace_promotion")
        promote_candidate(
            state_root=self.state_root,
            parent=parent,
            candidate=champion,
            candidate_source=student_root / "workspace" / "source",
            candidate_artifacts=artifacts,
        )
        self._cache_execution_champion_baseline(
            champion=champion,
            candidate=candidate,
            artifacts=artifacts,
        )
        evidence = load_json(candidate_path.parent / "evidence.json", {}) or {}
        history_path = self.state_root / "knowledge" / "execution_champion.json"
        prior = load_json(history_path, {}) or {}
        history = list(prior.get("history") or []) if isinstance(prior, Mapping) else []
        decision = {
            "adopted_after_round": self._last_round(),
            "result_id": champion.parent_id,
            "candidate_artifact": str(candidate_path),
            "source_parent_before": parent.to_dict(),
            "execution_parent_after": champion.to_dict(),
            "timing_recipe_id": champion.timing_recipe_id,
            "evidence_state": evidence.get("state"),
            "source_attribution": "preserved_from_candidate_evidence",
            "admission": "strictly_smaller_frozen_three_metric_distance_with_build_flow_metrics_lec_zero_drv",
            "runtime_and_observer_scores_used": False,
        }
        history.append(decision)
        atomic_json(
            history_path,
            {
                "schema_version": "goalevolve.v2.execution-champion.v1",
                "current": decision,
                "history": history[-100:],
            },
        )
        atomic_json(self.state_root / "parent.json", champion.to_dict())
        print(
            f"[GoalEvolve][execution-champion] adopted={champion.parent_id} "
            f"recipe={champion.timing_recipe_id} distance={champion.goal_distance:.8f} "
            f"source_attribution={evidence.get('state') or 'unknown'}",
            flush=True,
        )
        return champion

    def _cache_execution_champion_baseline(
        self,
        *,
        champion: Parent,
        candidate: Mapping[str, object],
        artifacts: Mapping[str, str],
    ) -> None:
        """Reuse the champion's completed run as its own no-diff recipe baseline."""
        suffix = self._baseline_cache_suffix(
            mode=champion.evaluation_mode,
            recipe_id=champion.timing_recipe_id,
        )
        root = (
            self.state_root
            / "stage_baselines"
            / f"{champion.source_hash}_{champion.evaluation_mode}_{champion.timing_recipe_id}_{suffix}"
        )
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "goalevolve.v2.execution-champion-baseline.v1",
            "parent_id": champion.parent_id,
            "source_hash": champion.source_hash,
            "evaluation_mode": champion.evaluation_mode,
            "timing_recipe_id": champion.timing_recipe_id,
            "ok": True,
            "metrics": dict(champion.metrics),
            "checks": list(candidate.get("checks") or []),
            "artifacts": dict(artifacts),
            "provenance": "same completed source+recipe run adopted as execution parent",
            "rerun_performed": False,
        }
        atomic_json(root / "baseline.json", payload)
        atomic_json(
            root / "parent_recipe_baseline.json",
            {
                "parent_before": champion.to_dict(),
                "parent_after": champion.to_dict(),
                "evaluation_mode": champion.evaluation_mode,
                "timing_recipe_id": champion.timing_recipe_id,
                "baseline": payload,
            },
        )

    def _stage_matched_parent(
        self,
        *,
        parent: Parent,
        decision_context: dict[str, object],
    ) -> Parent:
        """Return parent metrics measured by the exact active Tcl sequence.

        This matters in both directions.  An imported ``power_then_timing``
        result cannot be compared directly with a ``power_only`` Student, and
        the first ``repair_timing -setup`` pass at the later timing transition
        is likewise part of the controller sequence rather than evidence for
        whichever Student happens to be evaluated first.
        """
        mode = str(decision_context.get("evaluation_mode") or "")
        # Re-measure only when the actual evaluation sequence changes stage.
        # Controller/source revisions within the same mode are the object of
        # evolution and do not justify repeatedly spending a full baseline
        # flow. Runtime and Sfinal are observer-only.
        if not mode or parent.evaluation_mode == mode:
            return parent
        cache_suffix = self._baseline_cache_suffix(mode=mode)
        measure = getattr(self.evaluator, "evaluate_parent", None)
        if not callable(measure):
            raise RuntimeError("stage_requires_same_mode_parent_baseline")
        source = self.state_root / "parents" / parent.source_hash / "source"
        if not source.is_dir():
            raise RuntimeError(
                f"stage_parent_source_missing:{source}"
            )
        baseline_root = (
            self.state_root
            / "stage_baselines"
            / f"{parent.source_hash}_{mode}_{cache_suffix}"
        )
        baseline_path = baseline_root / "baseline.json"
        if not baseline_path.is_file():
            compatible = self._compatible_cached_baseline(
                parent=parent,
                mode=mode,
                recipe_id="legacy_setup",
            )
            if compatible is not None:
                baseline_root = compatible
                baseline_path = compatible / "baseline.json"
                print(
                    f"[GoalEvolve][stage-baseline] reuse_compatible_cache={baseline_path}",
                    flush=True,
                )
        payload = load_json(baseline_path, {}) if baseline_path.is_file() else {}
        if not isinstance(payload, dict) or not bool(payload.get("ok")):
            print(
                f"[GoalEvolve][stage-baseline] start parent={parent.parent_id} mode={mode}",
                flush=True,
            )
            payload = measure(
                contract=self.contract,
                parent=parent,
                source=source,
                output=baseline_root,
                optimization_mode=mode,
            )
            atomic_json(baseline_path, payload)
        if not bool(payload.get("ok")):
            raise RuntimeError(
                f"stage_parent_baseline_failed:{baseline_path}"
            )
        metrics = {
            str(name): float(value)
            for name, value in dict(payload.get("metrics") or {}).items()
            if isinstance(value, (int, float))
        }
        required = {metric.name for metric in self.contract.metrics}
        if not required.issubset(metrics):
            raise RuntimeError(
                "stage_parent_baseline_missing_metrics:"
                + ",".join(sorted(required - set(metrics)))
            )
        distance, _, _ = self.contract.evaluate(metrics)
        staged = replace(
            parent,
            metrics=metrics,
            goal_distance=distance,
            evaluation_mode=mode,
        )
        # Diagnosis must consume checkpoints produced by this exact mode, not
        # an imported source's previous Tcl schedule.  The source snapshot is
        # immutable; these two sidecar files are the current controller-mode
        # observation and are replaced again on a later stage transition.
        artifacts = dict(payload.get("artifacts") or {})
        parent_artifact_root = source.parent
        for artifact_key, destination_name in (
            ("checkpoint_metrics", "checkpoints.json"),
            ("checkpoint_post_route_db", "design_checkpoint.odb"),
        ):
            artifact = Path(str(artifacts.get(artifact_key) or ""))
            if artifact.is_file():
                shutil.copy2(artifact, parent_artifact_root / destination_name)
        atomic_json(
            baseline_root / "parent_stage_baseline.json",
            {
                "parent_before": parent.to_dict(),
                "parent_after": staged.to_dict(),
                "evaluation_mode": mode,
                "baseline": payload,
            },
        )
        print(
            f"[GoalEvolve][stage-baseline] complete parent={parent.parent_id} mode={mode} distance={distance:.8f}",
            flush=True,
        )
        return staged

    def _recipe_matched_parent(
        self,
        *,
        parent: Parent,
        hypothesis,
        decision_context: dict[str, object],
    ) -> tuple[Parent, str]:
        """Measure the no-diff parent for a candidate's exact timing recipe.

        A schedule is an intervention just like a source edit.  The only
        valid attribution comparison is therefore candidate vs. same-source,
        same-recipe parent—not vs. the default `repair_timing -setup` run.
        """
        stage = str(decision_context.get("stage") or "")
        power_recipe = (
            stage == "power_reclaim"
            and str(hypothesis.timing_recipe_id or "") == "rmp_area_power"
        )
        if stage not in {"timing_recovery", "adaptive_tradeoff"} and not power_recipe:
            return parent, "not_required"
        mode = str(hypothesis.evaluation_mode or "")
        if mode != "power_then_timing" and not (power_recipe and mode == "power_only"):
            return parent, "not_required"
        recipe_id = str(hypothesis.timing_recipe_id or "legacy_setup")
        measure = getattr(self.evaluator, "evaluate_parent", None)
        if not callable(measure):
            raise RuntimeError("timing_recipe_requires_parent_baseline_evaluator")
        source = self.state_root / "parents" / parent.source_hash / "source"
        if not source.is_dir():
            raise RuntimeError(f"timing_recipe_parent_source_missing:{source}")
        baseline_root = (
            self.state_root
            / "stage_baselines"
            / f"{parent.source_hash}_{mode}_{recipe_id}_{self._baseline_cache_suffix(mode=mode, recipe_id=recipe_id)}"
        )
        baseline_path = baseline_root / "baseline.json"
        if not baseline_path.is_file():
            compatible = self._compatible_cached_baseline(
                parent=parent,
                mode=mode,
                recipe_id=recipe_id,
            )
            if compatible is not None:
                baseline_root = compatible
                baseline_path = compatible / "baseline.json"
                print(
                    f"[GoalEvolve][recipe-baseline] reuse_compatible_cache={baseline_path}",
                    flush=True,
                )
        baseline_was_cached = baseline_path.is_file()
        payload = load_json(baseline_path, {}) if baseline_was_cached else {}
        if not baseline_was_cached or not isinstance(payload, dict):
            print(f"[GoalEvolve][recipe-baseline] start parent={parent.parent_id} recipe={recipe_id}", flush=True)
            payload = measure(
                contract=self.contract,
                parent=parent,
                source=source,
                output=baseline_root,
                optimization_mode=mode,
                timing_recipe_id=recipe_id,
            )
            atomic_json(baseline_path, payload)
        if not bool(payload.get("ok")):
            reason = str(payload.get("error") or payload.get("evaluation_error") or "engineering_failure")
            status = f"unavailable:{baseline_path}:{reason}"
            atomic_json(
                baseline_root / "parent_recipe_baseline_unavailable.json",
                {
                    "parent": parent.to_dict(),
                    "evaluation_mode": mode,
                    "timing_recipe_id": recipe_id,
                    "baseline": payload,
                    "fallback_parent": parent.to_dict(),
                    "promotion_authority": "lineage_only",
                },
            )
            print(
                f"[GoalEvolve][recipe-baseline] unavailable parent={parent.parent_id} recipe={recipe_id} fallback=lineage cached={str(baseline_was_cached).lower()}",
                flush=True,
            )
            return parent, status
        metrics = {
            str(name): float(value)
            for name, value in dict(payload.get("metrics") or {}).items()
            if isinstance(value, (int, float))
        }
        required = {metric.name for metric in self.contract.metrics}
        if not required.issubset(metrics):
            missing = ",".join(sorted(required - set(metrics)))
            status = f"unavailable:{baseline_path}:missing_metrics:{missing}"
            print(
                f"[GoalEvolve][recipe-baseline] unavailable parent={parent.parent_id} recipe={recipe_id} missing_metrics={missing} fallback=lineage",
                flush=True,
            )
            return parent, status
        distance, _, _ = self.contract.evaluate(metrics)
        staged = replace(parent, metrics=metrics, goal_distance=distance, evaluation_mode=mode)
        atomic_json(
            baseline_root / "parent_recipe_baseline.json",
            {
                "parent_before": parent.to_dict(),
                "parent_after": staged.to_dict(),
                "evaluation_mode": mode,
                "timing_recipe_id": recipe_id,
                "baseline": payload,
            },
        )
        print(f"[GoalEvolve][recipe-baseline] complete parent={parent.parent_id} recipe={recipe_id} distance={distance:.8f}", flush=True)
        return staged, f"matched:{baseline_path}"

    def _baseline_cache_suffix(self, *, mode: str, recipe_id: str | None = None) -> str:
        """Version parent baseline artifacts by the actual evaluator schedule.

        Source hash alone is insufficient: changing controller-owned Tcl can
        change a no-diff result without changing the parent source tree.
        Evaluators may expose a structured identity; conservative plugins that
        do not do so retain the legacy-mode identity.
        """
        identity = getattr(self.evaluator, "baseline_identity", None)
        if callable(identity):
            goal_tns_abs_ns = next(
                (
                    float(metric.target)
                    for metric in self.contract.metrics
                    if metric.name == "tns_abs_ns"
                ),
                None,
            )
            payload = identity(
                optimization_mode=mode,
                timing_recipe_id=recipe_id,
                goal_tns_abs_ns=goal_tns_abs_ns,
            )
        else:
            payload = {"schema": "legacy-evaluator-baseline.v1"}
        return sha256_json(
            {
                "mode": mode,
                "recipe_id": recipe_id,
                "evaluator": getattr(self.evaluator, "name", type(self.evaluator).__name__),
                "identity": payload,
            }
        )[:16]

    def _compatible_cached_baseline(
        self,
        *,
        parent: Parent,
        mode: str,
        recipe_id: str,
    ) -> Path | None:
        """Find an older cache proven to have byte-equivalent controller Tcl.

        Cache schema upgrades must not rerun an unchanged no-diff flow.  The
        evaluator performs the schedule-specific proof; the engine also checks
        parent source, mode, and recipe metadata before accepting the alias.
        """
        compatible = getattr(self.evaluator, "baseline_artifact_compatible", None)
        root = self.state_root / "stage_baselines"
        if not callable(compatible) or not root.is_dir():
            return None
        goal_tns_abs_ns = next(
            (
                float(metric.target)
                for metric in self.contract.metrics
                if metric.name == "tns_abs_ns"
            ),
            None,
        )
        matches: list[tuple[int, Path]] = []
        for candidate_root in root.glob(f"{parent.source_hash}_{mode}_*"):
            baseline_path = candidate_root / "baseline.json"
            if not baseline_path.is_file():
                continue
            payload = load_json(baseline_path, {}) or {}
            if not isinstance(payload, Mapping):
                continue
            source_hash = str(payload.get("source_hash") or parent.source_hash)
            candidate_mode = str(payload.get("evaluation_mode") or mode)
            candidate_recipe = str(payload.get("timing_recipe_id") or "legacy_setup")
            if (
                source_hash != parent.source_hash
                or candidate_mode != mode
                or candidate_recipe != recipe_id
            ):
                continue
            if not compatible(
                baseline_root=candidate_root,
                optimization_mode=mode,
                timing_recipe_id=recipe_id,
                goal_tns_abs_ns=goal_tns_abs_ns,
            ):
                continue
            matches.append((baseline_path.stat().st_mtime_ns, candidate_root))
        return min(matches, default=(0, None), key=lambda item: item[0])[1]

    def _record_timing_schedule_memory(self, *, round_index: int, rows: Sequence[dict[str, Any]]) -> None:
        records: list[dict[str, object]] = []
        for row in rows:
            candidate = row["candidate"]
            tradeoff: dict[str, object] = {}
            tradeoff_path = Path(str(candidate.artifacts.get("power_timing_cell_tradeoff") or ""))
            if tradeoff_path.is_file():
                payload = load_json(tradeoff_path, {})
                if isinstance(payload, dict):
                    tradeoff = payload
            if (
                candidate.hypothesis.evaluation_mode != "power_then_timing"
                and not bool(tradeoff.get("power_reclaim_available"))
            ):
                continue
            checkpoint_metrics: dict[str, object] = {}
            checkpoint_path = Path(str(candidate.artifacts.get("checkpoint_metrics") or ""))
            if checkpoint_path.is_file():
                payload = load_json(checkpoint_path, {})
                if isinstance(payload, dict):
                    checkpoint_metrics = payload
            comparison_parent = row["comparison_parent"]
            verdict = row["verdict"]
            records.append(
                {
                    "round": round_index,
                    "student_id": candidate.student_id,
                    "evaluation_mode": candidate.hypothesis.evaluation_mode,
                    "recipe_id": (
                        candidate.hypothesis.timing_recipe_id
                        if candidate.hypothesis.evaluation_mode == "power_then_timing"
                        else "power_only"
                    ),
                    "hypothesis_id": candidate.hypothesis.hypothesis_id,
                    "repair_timing_command": (
                        timing_recipe(candidate.hypothesis.timing_recipe_id).repair_timing_command()
                        if candidate.hypothesis.evaluation_mode == "power_then_timing"
                        else None
                    ),
                    "comparison_parent": comparison_parent.to_dict(),
                    "metrics": dict(candidate.metrics),
                    "verdict": verdict.to_dict(),
                    "checkpoint_metrics": checkpoint_metrics,
                    "cell_tradeoff": tradeoff,
                }
            )
        if records:
            record_schedule_memory(self.state_root, records)

    def _last_round(self) -> int:
        rounds_root = self.state_root / "rounds"
        found = [
            int(path.name.split("_")[-1])
            for path in rounds_root.glob("round_*")
            if path.name.split("_")[-1].isdigit() and (path / "round.json").is_file()
        ] if rounds_root.is_dir() else []
        return max(found, default=0)

    def _parent_checkpoints(self, parent: Parent) -> dict[str, dict[str, float]]:
        def decode(path: Path) -> dict[str, dict[str, float]]:
            payload = load_json(path, {}) or {}
            return {
                str(stage): {str(name): float(value) for name, value in dict(metrics).items() if isinstance(value, (int, float))}
                for stage, metrics in dict(payload.get("checkpoints") or {}).items()
                if isinstance(metrics, dict)
            }

        local = decode(self.state_root / "parents" / parent.source_hash / "checkpoints.json")
        if local:
            return local
        # The baseline is measured before it is a promoted candidate, so its
        # checkpoint artifact lives in the EPD rather than a parent snapshot.
        # Falling back to that authoritative same-flow artifact keeps Teacher
        # diagnosis and prompts checkpoint-grounded from round one onward.
        for record in self._epd().records():
            if str(record.get("parent_id") or "") != parent.parent_id:
                continue
            if str(record.get("source_hash") or "") != parent.source_hash:
                continue
            checkpoint = Path(str(dict(record.get("artifacts") or {}).get("checkpoint_metrics") or ""))
            recovered = decode(checkpoint)
            if recovered:
                return recovered
        return {}

    def _previous_teacher_review(
        self,
        round_index: int,
        *,
        parent: Parent | None = None,
    ) -> dict[str, object]:
        """Pass prior guidance, not another copy of its full evidence packet.

        A review already embeds EPD, observations and schedule history.  Feeding
        that whole review into the next plan duplicates those artifacts (and
        the cell-level tradeoff examples) on every round.  The Teacher needs
        its decisions and the immediately observed verdicts; it receives fresh
        compact EPD/observation/schedule summaries separately.
        """
        if round_index <= 1:
            return {}
        path = self.state_root / "rounds" / f"round_{round_index - 1:03d}" / "teacher_review.json"
        payload = load_json(path, {}) or {}
        if not isinstance(payload, dict):
            return {}
        quarantine = load_json(
            self.state_root / "knowledge" / "evidence_quarantine.json", {}
        ) or {}
        excluded = {str(item) for item in list(quarantine.get("hypothesis_ids") or [])}
        epd_status_by_hypothesis = {
            str(row.get("hypothesis_id") or ""): str(row.get("epd_status") or "")
            for row in self._epd().records()
            if str(row.get("hypothesis_id") or "")
        }
        outcomes = []
        for row in list(payload.get("outcomes") or []):
            if not isinstance(row, dict):
                continue
            hypothesis = dict(row.get("hypothesis") or {})
            hypothesis_id = str(
                row.get("hypothesis_id") or hypothesis.get("hypothesis_id") or ""
            )
            if hypothesis_id in excluded:
                continue
            outcomes.append(
                {
                    "student_id": row.get("student_id"),
                    "hypothesis_id": hypothesis_id,
                    "mechanism_family": row.get("mechanism_family") or hypothesis.get("mechanism_family"),
                    "verdict": row.get("verdict"),
                    "epd_lifecycle": epd_status_by_hypothesis.get(hypothesis_id, ""),
                }
            )
        parsed_review = payload.get("parsed_markdown") or {}
        raw_review = parsed_review if isinstance(parsed_review, Mapping) else {}
        if not raw_review:
            raw_review = payload.get("raw_review") or {}
        # Free-form review prose cannot be selectively redacted.  If this
        # round contains quarantined controller evidence, omit that prose and
        # pass only the remaining structured outcomes; otherwise a corrected
        # next-round allocation can be suppressed again by the stale text.
        if any(
            str(dict(row.get("hypothesis") or {}).get("hypothesis_id") or "") in excluded
            for row in list(payload.get("outcomes") or [])
            if isinstance(row, dict)
        ):
            raw_review = {
                "quarantined": True,
                "reason": "prior review depended on quarantined controller evidence",
            }
        unactivated = [
            row for row in outcomes if str(row.get("epd_lifecycle") or "") == "unactivated"
        ]
        if unactivated:
            # The review was generated before the lifecycle repair and may
            # call a non-firing mechanism "suppressed". EPD's current
            # source-backed lifecycle is authoritative for the next plan.
            raw_review = {
                "superseded": True,
                "reason": "current EPD lifecycle marks one or more completed attempts unactivated; prior suppression prose has no authority until an executing recipe is measured",
                "unactivated_hypothesis_ids": [str(row["hypothesis_id"]) for row in unactivated],
            }
        committed_round = load_json(path.parent / "round.json", {}) or {}
        committed_parent = dict(committed_round.get("parent_after") or {})
        controller_changed_parent = (
            parent is not None
            and str(committed_parent.get("parent_id") or "") != parent.parent_id
        )
        if controller_changed_parent:
            raw_review = {
                "superseded": True,
                "reason": "a closer fully checked execution champion became the authoritative search parent after this review",
                "previous_parent_id": committed_parent.get("parent_id"),
                "current_parent": parent.to_dict(),
            }
        return {
            "schema_version": "goalevolve.v2.teacher-review-guidance.v1",
            "teacher_ok": bool(payload.get("teacher_ok")),
            "diagnosis": payload.get("diagnosis") or {},
            "raw_review": raw_review,
            "teacher_markdown": "" if unactivated else (payload.get("teacher_markdown") or ""),
            "outcomes": outcomes,
            # The deterministic controller—not review prose—owns lineage.
            # Supplying the committed round decision prevents a later Teacher
            # from asking to undo a verified_qor_unattributed promotion or to
            # rerun a cached failed matched baseline.
            "controller_decision": {
                "promoted_student": committed_round.get("promoted_student"),
                "parent_after": parent.to_dict() if controller_changed_parent and parent is not None else committed_parent,
                "authority": "execution_champion" if controller_changed_parent else "committed_round_json",
            },
        }
