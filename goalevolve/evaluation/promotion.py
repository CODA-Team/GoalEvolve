from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from ..core.contracts import GoalContract
from .evidence import REQUIRED_CHECKS, classify_candidate
from ..core.models import CandidateResult, EvidenceVerdict, Parent
from ..planning.timing_recovery import TIMING_RECOVERY_RECIPES


class StrictEvidencePromotion:
    """Promote verified QoR; retain causal-attribution status as separate evidence."""

    name = "strict_evidence"

    def classify(self, *, contract: GoalContract, parent: Parent, candidate: CandidateResult) -> EvidenceVerdict:
        return classify_candidate(contract=contract, parent=parent, candidate=candidate)

    def choose(self, rows: Sequence[tuple[CandidateResult, EvidenceVerdict]]) -> tuple[CandidateResult, EvidenceVerdict] | None:
        valid = [
            row
            for row in rows
            if row[1].state in {"validated", "verified_qor_unattributed"}
        ]
        return max(valid, key=lambda row: row[1].distance_gain) if valid else None


class PowerFirstPromotion:
    """Two-stage controller: reach power targets first, then repair timing.

    Stage 1 is deliberately not a weakened final contract.  It admits only
    complete official evidence and zero DRV, ranks candidates lexicographically
    by the residuals of leakage and dynamic power, and bounds timing with an
    explicit safety ceiling.  Once both power targets are met, Stage 2 returns
    to the frozen three-metric contract and seeks timing recovery from that
    low-power parent.
    """

    name = "power_first_promotion"
    power_metrics = ("leakage_power_pw", "dynamic_power_pw")

    def __init__(
        self,
        *,
        power_stage_tns_ceiling_ns: float = 30.0,
        protected_power_rounds: int = 10,
    ) -> None:
        if power_stage_tns_ceiling_ns <= 0:
            raise ValueError("power_stage_tns_ceiling_ns must be positive")
        if protected_power_rounds < 0:
            raise ValueError("protected_power_rounds cannot be negative")
        self.power_stage_tns_ceiling_ns = float(power_stage_tns_ceiling_ns)
        self.protected_power_rounds = int(protected_power_rounds)

    def stage(self, *, contract: GoalContract, parent: Parent) -> str:
        return "timing_recovery" if self._power_complete(contract, parent.metrics) else "power_reclaim"

    def context(
        self,
        *,
        contract: GoalContract,
        parent: Parent,
        round_index: int | None = None,
    ) -> dict[str, object]:
        power_complete = self._power_complete(contract, parent.metrics)
        protected = round_index is None or round_index <= self.protected_power_rounds
        stage = (
            "timing_recovery"
            if power_complete
            else ("power_reclaim" if protected else "adaptive_tradeoff")
        )
        _, normalized_residuals, _ = contract.evaluate(parent.metrics)
        unresolved = {
            name: float(value)
            for name, value in normalized_residuals.items()
            if value is not None and float(value) > 0.0
        }
        dominant_metric = max(unresolved, key=unresolved.get) if unresolved else ""
        context: dict[str, object] = {
            "mode": "power_first",
            "stage": stage,
            "round_index": round_index,
            "protected_power_rounds": self.protected_power_rounds,
            "normalized_residuals": normalized_residuals,
            "dominant_metric": dominant_metric,
            "primary_metrics": (
                list(self.power_metrics)
                if stage == "power_reclaim"
                else ([dominant_metric] if dominant_metric else ["tns_abs_ns"])
            ),
            "power_stage_tns_ceiling_ns": self.power_stage_tns_ceiling_ns,
            "execution_champion_recipe_id": parent.timing_recipe_id,
            "execution_champion_parent_id": parent.parent_id,
            "promotion_rule": (
                "Strictly improve the lexicographic leakage/dynamic residual vector with complete official evidence and zero DRV; TNS may worsen only up to the explicit safety ceiling."
                if stage == "power_reclaim"
                else (
                    "After the protected power-only rounds, strictly improve both the dominant normalized residual and the frozen final three-metric distance; preserve every already-satisfied QoR target."
                    if stage == "adaptive_tradeoff"
                    else "Both power targets are met; strictly improve the frozen final three-metric contract while repairing timing."
                )
            ),
        }
        if stage == "power_reclaim":
            context.update(
                {
                    "evaluation_mode": "power_only",
                    "baseline_policy": "Use the current parent's complete official power_only metrics directly. Do not rerun a no-diff baseline within this mode; remeasure only on an actual stage or recipe transition.",
                    "full_contract_distance_required_for_promotion": False,
                    "falsification_rule": "Require strict lexicographic leakage/dynamic residual improvement, complete official evidence, zero DRV, and TNS within the safety ceiling; do not additionally require full three-metric distance or TNS to improve.",
                    "executed_command_boundary": "top-level repair_power command: Resizer.tcl -> Resizer.i -> Resizer::repairPower -> Optimizer(REPAIR_POWER) -> power-specialized policies",
                    "source_focus": ["src/rsz"],
                    "implementation_rule": "Treat the complete repair_power call chain under src/rsz as the power-reclaim subsystem, not just its first policy hook. A power hypothesis may modify its command dispatch, REPAIR_POWER optimizer configuration, dedicated policies, and their power-specific helpers. If a useful Setup/VT/size/upsize, buffering, parasitic, or routing-related mechanism currently exists only for repair_timing, copy or specialize it behind REPAIR_POWER and invoke that copy only from repair_power. Do not change ordinary repair_timing semantics to obtain a power effect; shared utilities may change only when their existing timing behavior is provably preserved.",
                    "stage1_experience": {
                        "stagnation_rule": "Do not remain in power_only after the configured protected round count when official leakage remains unchanged; transition to adaptive_tradeoff.",
                        "bounded_guard_relaxation": "After repeated fully activated no-gain experiments, relax only one evidence-identified repair_power admission boundary at a time. Preserve the absolute Stage-1 TNS ceiling, electrical guards, journal rollback, zero DRV, and official 4/4 LEC.",
                        "cell_accounting": "Persist every pre-power to post-power library-cell transition. In a later timing pass, separately count overlap and exact reversal so the Teacher can distinguish durable power moves from work immediately undone downstream.",
                        "area_restructure_option": "A controller-owned restructure target=area (RMP-A) is a distinct upstream recipe, not an implicit repair_power side effect. It requires its own no-diff matched baseline, bounded cone/snapshot/restore, explicit attempted/retained telemetry, and complete post-route evidence.",
                    },
                }
            )
        else:
            context.update(
                {
                    "evaluation_mode": "power_then_timing",
                    "timing_recovery_recipes": [recipe.to_dict() for recipe in TIMING_RECOVERY_RECIPES.values()],
                    "power_rebuild_rule": "Before every timing recipe, execute the retained low-power command exactly once: repair_power -phase early_forced_reclaim -proportion 80 (with the campaign-configured move cap) using the current inherited source. This is the R25 Student-1 power-reclaim command boundary, not an implicit side effect of repair_timing. Then checkpoint post_repair_power before any timing action.",
                    "schedule_rule": "The scheduler may allocate bounded LEGACY_MT, TNS, WNS/WNS_CONE, WNS_PATH, CRIT_VT_SWAP, REROUTE, MEASURED_VT, and MT1 recipes according to evidence. The authoritative recipe is the timing_recipe_id in each verified hypothesis slot, not a Student-number convention; it is controller-owned and immutable within its source experiment and must have a matching no-diff parent baseline. `reroute_mid_power` alone uses the valid repair_power -phase mid_area_reclaim command between bounded timing phases. Student source edits must not silently alter Tcl or substitute a recipe.",
                    "implementation_rule": "Use early rounds to obtain real schedule activation/outcome evidence, then place the main search budget on the repair_timing policy/dispatch selected by the assigned recipe. Preserve repair_power reconstruction. If timing repair reverses an earlier power cell replacement, emit or preserve the policy telemetry needed to explain that reversal; controller-side checkpoint snapshots will also measure and aggregate the instance-level overlap/reversion rate for the Teacher.",
                    "controller_revisions": {
                        "rmp_delay_timing": "single_combined_standard_cell_liberty_v3: RMP/ABC receives one generated standard-cell Liberty, runs before repair_timing, and evaluates up to four distinct endpoint clouds (RMP_MAX_CLOUDS=4) while retaining the one-accepted-cloud bound. R36 proved the v1 one-endpoint/two-instance cloud reaches real STA but has no useful mode; that evidence must not suppress this changed cloud-selection/schedule experiment, which requires a fresh matched no-diff baseline.",
                        "rmp_path_cone_timing": "rmp_path_cone_v4: R36 v3 established that four independently selected endpoint clouds are real but trivial (one to four instances) and all guarded STA trials regress. The v4 recipe instead unions at most four paths per unique endpoint and applies one bounded upstream-fanin halo (at most 16 additions), still tries at most four clouds and accepts at most one. This controller change and the path-cone source boundary require a fresh exact-recipe no-diff baseline; v3 refutation must not suppress it.",
                        "rmp_path_cone_halo_timing": "rmp_path_cone_halo_v5: R37 showed that v4 rejects every pre-halo one-to-four-instance core and then accidentally falls through to a 282-instance generic all-fanin blob; all its guarded STA trials regress. V5 owns the same bounded union/halo controller but explicitly blocks that generic fallback. Its source experiment must make admission after the bounded halo and preserve the path core. This is a new fallback/admission boundary and requires a fresh exact-recipe baseline; R37 must not suppress it."
                    },
                }
            )
            if stage == "adaptive_tradeoff":
                context.update(
                    {
                        "evaluation_mode": "power_then_timing",
                        "candidate_pool_coverage": {
                            "dominant_metric_candidates": 2,
                            "upstream_power_candidates": 1,
                            "power_timing_handoff_candidates": 1,
                            "rule": "Build a diverse controller-verified candidate menu covering the dominant residual, repair_power durability, and power-to-timing handoff. These are retrieval coverage groups, not Student roles. The Teacher assigns only the role envelopes supplied by the Controller: when no EPD role is eligible, all available slots are Explorers; otherwise the Controller retains its Explorer and eligible Integrator/Enhancer envelopes. An evidence-exhausted group narrows the menu rather than being relabeled as another mechanism type.",
                        },
                        "falsification_rule": "Require a strict reduction in the active dominant normalized residual and in full three-metric normalized distance, keep every metric already at target within target, require zero DRV and official 4/4 LEC, and compare each recipe only with its exact no-diff baseline.",
                        "baseline_policy": "The transition from power_only to power_then_timing is measured once. Every distinct controller-owned timing/RMP recipe then uses a cached exact-recipe no-diff parent baseline; never rerun an unchanged baseline.",
                    }
                )
        return context

    def classify(self, *, contract: GoalContract, parent: Parent, candidate: CandidateResult) -> EvidenceVerdict:
        if (
            candidate.hypothesis.evaluation_mode == "power_then_timing"
            or self.stage(contract=contract, parent=parent) == "timing_recovery"
        ):
            # Recipe baselines deliberately include a timing pass.  That pass
            # may consume some power headroom, but it must never cause the
            # controller to reclassify a timing-recovery experiment as a new
            # power-stage experiment.  Its contract is always the frozen
            # final three-metric contract.
            final_parent_distance, _, _ = contract.evaluate(parent.metrics)
            verdict = classify_candidate(
                contract=contract,
                parent=replace(parent, goal_distance=final_parent_distance),
                candidate=candidate,
            )
            if (
                candidate.hypothesis.evaluation_mode == "power_then_timing"
                and not self._power_complete(contract, parent.metrics)
            ):
                return self._adaptive_tradeoff_verdict(
                    contract=contract,
                    parent=parent,
                    candidate=candidate,
                    verdict=verdict,
                )
            return verdict

        distance, _, missing = self._power_distance(contract, candidate.metrics)
        parent_distance, _, parent_missing = self._power_distance(contract, parent.metrics)
        gain = parent_distance - distance
        power_key = self._power_key(contract, candidate.metrics)
        # Keep the exact Stage-1 ordering evidence with the candidate.  The
        # engine persists candidate artifacts, so this also makes a power-first
        # promotion auditable without turning Sfinal/SPPA into a decision term.
        candidate.artifacts["power_stage_key"] = ",".join(f"{value:.17g}" for value in power_key)
        check_map = {item.name: item.passed for item in candidate.checks}
        missing_checks = [name for name in REQUIRED_CHECKS if not check_map.get(name, False)]
        activation_signals = (
            candidate.hypothesis.activation_signals
            or candidate.hypothesis.expected_signals
        )
        mechanism_fired = bool(candidate.phase_signals) and all(
            abs(float(candidate.phase_signals.get(signal, 0.0))) > 0.0
            for signal in activation_signals
        )
        integrity_ok = (
            not candidate.evaluation_error
            and bool(candidate.implementation_diff.strip())
            and bool(candidate.source_commit.strip())
            and not missing_checks
            and not missing
        )
        reasons: list[str] = []
        tns = self._number(candidate.metrics.get("tns_abs_ns"))
        drv = self._number(candidate.metrics.get("drv_count"))
        if candidate.legacy:
            reasons.append("legacy_evidence_cannot_promote_without_revalidation")
            state = "legacy"
        elif not integrity_ok:
            reasons.extend(f"failed_or_missing_check:{name}" for name in missing_checks)
            reasons.extend(f"missing_power_metric:{name}" for name in missing)
            if not candidate.implementation_diff.strip():
                reasons.append("missing_implementation_diff")
            if not candidate.source_commit.strip():
                reasons.append("missing_source_commit")
            if candidate.evaluation_error:
                reasons.append(f"evaluation_error:{candidate.evaluation_error}")
            state = "invalid"
        elif drv is None or drv != 0.0:
            reasons.append(f"drv_not_zero:{drv}")
            state = "refuted"
        elif tns is None or tns > self.power_stage_tns_ceiling_ns:
            reasons.append(f"power_stage_tns_safety_ceiling:{tns}")
            state = "refuted"
        elif parent_missing:
            reasons.extend(f"missing_parent_power_metric:{name}" for name in parent_missing)
            state = "invalid"
        elif self._power_key(contract, candidate.metrics) >= self._power_key(contract, parent.metrics):
            reasons.append(f"power_residual_not_improved:{gain:.6g}")
            state = "refuted"
        elif not mechanism_fired:
            reasons.append("verified_power_gain_with_missing_mechanism_telemetry")
            state = "verified_qor_unattributed"
        else:
            reasons.append("official_checks_passed_and_power_residual_improved")
            state = "validated"
        return EvidenceVerdict(state, distance, gain, mechanism_fired, integrity_ok, tuple(reasons))

    def choose(self, rows: Sequence[tuple[CandidateResult, EvidenceVerdict]]) -> tuple[CandidateResult, EvidenceVerdict] | None:
        valid = [row for row in rows if row[1].state in {"validated", "verified_qor_unattributed"}]
        if valid and any(row[0].hypothesis.evaluation_mode == "power_then_timing" for row in valid):
            # Recipe baselines differ by design.  Once each candidate has
            # proven a positive matched-baseline gain, retain the source plus
            # recipe with the smallest real final frozen-goal distance.
            return min(valid, key=lambda row: row[1].goal_distance)
        # `goal_distance` is intentionally the residual sum for reporting and
        # continuity with the normal contract artifacts.  It is not the
        # Stage-1 preference relation: a smaller sum can hide a worse worst
        # unresolved power target.  Select with the same lexicographic vector
        # that admission uses so the promoted low-power lineage is coherent.
        return min(valid, key=lambda row: self._power_key_from_candidate(row[0])) if valid else None

    def _power_key_from_candidate(self, candidate: CandidateResult) -> tuple[float, float, float, float]:
        # The contract is intentionally not stored on CandidateResult.  The
        # classification pass records the normalized key before `choose()` is
        # called; it is then part of the candidate's persisted audit record.
        raw = candidate.artifacts.get("power_stage_key", "")
        try:
            values = tuple(float(value) for value in raw.split(","))
            if len(values) == 4:
                return values  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass
        return (float("inf"),) * 4

    def _adaptive_tradeoff_verdict(
        self,
        *,
        contract: GoalContract,
        parent: Parent,
        candidate: CandidateResult,
        verdict: EvidenceVerdict,
    ) -> EvidenceVerdict:
        """Prevent post-protection tradeoffs from hiding a new target miss.

        Full normalized distance is the cross-metric objective, but it alone
        can buy a large TNS gain by giving back an already achieved power
        target.  The adaptive stage therefore requires two simultaneous facts:
        the parent's largest normalized residual decreases, and every metric
        already at target remains at target.  Unresolved secondary metrics may
        trade only when the full distance still improves.
        """
        if verdict.state not in {"validated", "verified_qor_unattributed"}:
            return verdict
        _, parent_residuals, parent_missing = contract.evaluate(parent.metrics)
        _, candidate_residuals, candidate_missing = contract.evaluate(candidate.metrics)
        if parent_missing or candidate_missing:
            return verdict
        unresolved = {
            name: float(value)
            for name, value in parent_residuals.items()
            if value is not None and float(value) > 0.0
        }
        reasons = list(verdict.reasons)
        violations: list[str] = []
        for name, residual in parent_residuals.items():
            if residual is None or float(residual) > 0.0:
                continue
            candidate_residual = candidate_residuals.get(name)
            if candidate_residual is not None and float(candidate_residual) > 0.0:
                violations.append(f"adaptive_regressed_satisfied_target:{name}")
        if unresolved:
            dominant = max(unresolved, key=unresolved.get)
            candidate_dominant = float(candidate_residuals.get(dominant) or 0.0)
            if candidate_dominant >= unresolved[dominant] - 1e-9:
                violations.append(
                    f"adaptive_dominant_residual_not_improved:{dominant}:"
                    f"{unresolved[dominant]:.9g}->{candidate_dominant:.9g}"
                )
        tns = self._number(candidate.metrics.get("tns_abs_ns"))
        if tns is None or tns > self.power_stage_tns_ceiling_ns:
            violations.append(f"adaptive_tns_safety_ceiling:{tns}")
        if not violations:
            return verdict
        reasons.extend(violations)
        return EvidenceVerdict(
            "refuted",
            verdict.goal_distance,
            verdict.distance_gain,
            verdict.mechanism_fired,
            verdict.integrity_ok,
            tuple(reasons),
        )

    def _power_complete(self, contract: GoalContract, metrics: Mapping[str, object]) -> bool:
        _, residuals, missing = self._power_distance(contract, metrics)
        return not missing and all(float(residuals[name] or 0.0) <= 0.0 for name in self.power_metrics)

    def _power_distance(self, contract: GoalContract, metrics: Mapping[str, object]) -> tuple[float, dict[str, float | None], list[str]]:
        residuals: dict[str, float | None] = {}
        missing: list[str] = []
        for name in self.power_metrics:
            spec = next((item for item in contract.metrics if item.name == name), None)
            if spec is None:
                missing.append(name)
                residuals[name] = None
                continue
            residual = spec.residual(self._number(metrics.get(name)))
            residuals[name] = residual
            if residual is None:
                missing.append(name)
        if missing:
            return float("inf"), residuals, missing
        return sum(float(residuals[name] or 0.0) for name in self.power_metrics), residuals, missing

    def _power_key(self, contract: GoalContract, metrics: Mapping[str, object]) -> tuple[float, float, float, float]:
        _, residuals, missing = self._power_distance(contract, metrics)
        if missing:
            return (float("inf"),) * 4
        leakage = float(residuals["leakage_power_pw"] or 0.0)
        dynamic = float(residuals["dynamic_power_pw"] or 0.0)
        # Minimize the worst unresolved power target before trading between
        # them; this prevents a dynamic-only win from sacrificing leakage.
        return (max(leakage, dynamic), leakage + dynamic, leakage, dynamic)

    @staticmethod
    def _number(value: object) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
