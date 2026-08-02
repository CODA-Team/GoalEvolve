from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence

from ..core.io import atomic_json, load_json, sha256_json
from ..core.models import Hypothesis, Parent
from .epd import EvolutionProgramDatabase
from .timing_recovery import recipes_for_students


@dataclass(frozen=True)
class MechanismCard:
    card_id: str
    mechanism_family: str
    symptom_tags: tuple[str, ...]
    source_hooks: tuple[str, ...]
    expected_signals: tuple[str, ...]
    claim_template: str
    evidence_level: str = "source"
    scope: str = "generic"
    required_flow_commands: tuple[str, ...] = ()
    # Useful instrumentation is retained as knowledge, but a source edit that
    # deliberately leaves optimization behavior unchanged cannot justify a
    # full Student/post-route QoR evaluation.
    candidate_eligible: bool = True
    # A card can be a bounded repair of an empirically activated but rejected
    # mechanism.  These are priorities, not eligibility gates: the source
    # hook still has to pass normal scope verification.
    refinement_signals: tuple[str, ...] = ()
    # Stage-1 cards must prove they affect the top-level `repair_power`
    # command path, rather than an implicit tail in `repair_timing`.
    power_command_eligible: bool = False
    # If provided, this is the strict minimum telemetry needed to establish
    # execution.  ``expected_signals`` remains the complete observability
    # schema and may include conditionally-zero outcome counters.
    activation_signals: tuple[str, ...] = ()
    # Required substrings in the completed evaluation log that prove an
    # activation condition was exhausted under this card's fixed recipe.
    # They prevent a telemetry-retry loop from replaying the same experiment.
    conclusive_nonactivation_patterns: tuple[str, ...] = ()


DEFAULT_CARDS = (
    MechanismCard(
        "timing_commit_guard",
        "commit_guard",
        ("tns", "wns", "rollback"),
        ("src/rsz/src/MoveCommitter.cc",),
        ("accepted_commit", "rejected_timing"),
        "Use an explicit rollback guard so a committed move cannot consume the final timing budget.",
    ),
    MechanismCard(
        "power_candidate_ranking",
        "candidate_ranking",
        ("leakage", "power", "vt"),
        ("src/rsz/src/RecoverPower.cc",),
        ("accepted_vt_swap", "leakage_delta"),
        "Rank power-recovery candidates by measured leakage benefit subject to timing reserve.",
        required_flow_commands=("recover_power",),
    ),
    MechanismCard(
        "setup_crit_vt_power_balance",
        "setup_vt_power_balance",
        ("leakage", "power", "tns", "timing"),
        ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc",),
        ("crit_vt_power_moves", "crit_vt_leakage_delta"),
        "At the executed repair_timing CRIT_VT_SWAP phase, add one bounded policy decision that records the count and signed static-leakage delta of committed critical VT swaps. Use the existing timing-power-aware scoring boundary; do not change Tcl, policy phase names, or a global timing threshold. The experiment is falsified if the phase does not execute, no committed move is observed, or the measured final leakage does not improve while preserving timing.",
    ),
    MechanismCard(
        "setup_tns_leakage_reserve",
        "setup_leakage_reserve",
        ("leakage", "power", "tns", "timing"),
        ("src/rsz/src/policy/SetupTnsPolicy.cc",),
        ("tns_reserve_rejections", "tns_power_safe_moves"),
        "At the executed repair_timing TNS phase, use the parent timing reserve only to reject a bounded move that has a negative static-leakage tradeoff, and emit separate counters for rejected and accepted power-safe moves. Preserve the existing phase ordering and do not alter Tcl or benchmarks. A no-signal result is activation failure, not a basis for threshold sweeping.",
    ),
    MechanismCard(
        "optimizer_phase_power_attribution",
        "optimizer_phase_attribution",
        ("leakage", "power", "runtime", "timing"),
        ("src/rsz/src/Optimizer.cc",),
        ("repair_timing_phase_started", "repair_timing_phase_completed"),
        "At the executed Optimizer phase loop, add bounded phase-boundary telemetry around the existing repair_timing policy sequence so a later leakage edit can be attributed to an actually executed phase. Do not add or remove phases, modify Tcl, or claim QoR benefit from telemetry alone.",
        candidate_eligible=False,
    ),
    MechanismCard(
        "implicit_power_recovery_plus",
        "implicit_power_recovery_plus",
        ("leakage", "power", "dynamic", "timing"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/policy/PowerRecoveryPlusPolicy.cc"),
        ("power_recovery_plus_committed", "power_recovery_plus_leakage_gain"),
        "The fixed contest Tcl invokes `repair_timing -setup` without `-phases`, so its actual pipeline is Optimizer's default `LEGACY LAST_GASP` plus the implicit CRIT_VT_SWAP tail. Make one bounded source-level activation of the already implemented POWER_RECOVERY_PLUS policy only for this implicit default pipeline, after timing repair and before final reporting. Give its OptimizerRunConfig the existing `early_forced_reclaim` profile so it performs a real, timing-guarded power-recovery search; do not change Tcl, benchmarks, command arguments, or globally replace custom `-phases` schedules. Preserve a finite timing/rollback guard and emit METRIC|power_recovery_plus_committed|<nonzero committed count> and METRIC|power_recovery_plus_leakage_gain|<nonzero positive local leakage gain> only when retained moves exist. The experiment is refuted if the final official dynamic/leakage power is unchanged or timing regresses beyond the frozen contract.",
    ),
    MechanismCard(
        "implicit_repair_power_reclaim",
        "implicit_repair_power_reclaim",
        ("leakage", "power", "dynamic", "timing"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/policy/RepairPowerPolicy.cc"),
        ("repair_power_committed", "repair_power_leakage_gain"),
        "The fixed contest Tcl leaves Optimizer::config_.phases empty, which selects the default repair_timing pipeline. At that exact default-only dispatch boundary, evaluate the existing REPAIR_POWER policy as a bounded post-repair reclaim phase using its already implemented `early_forced_reclaim` configuration, while leaving all explicit custom phase lists untouched. Retain only moves passing the policy's timing journal and do not call an uninvoked Tcl command or edit benchmark inputs. Emit METRIC|repair_power_committed|<nonzero retained move count> and METRIC|repair_power_leakage_gain|<nonzero positive local leakage gain> only after actual retained moves. Do not add telemetry-only code: the edit must make this existing policy reachable in the executed flow.",
    ),
    MechanismCard(
        "power_recovery_policy_final_guard",
        "power_recovery_policy_guard",
        ("leakage", "power", "dynamic", "timing"),
        ("src/rsz/src/policy/PowerRecoveryPlusPolicy.cc",),
        ("power_recovery_guarded_moves", "power_recovery_retained_gain"),
        "The candidate source already contains PowerRecoveryPlusPolicy, but its default legacy profile is conservative and is never selected by the contest's implicit phase pipeline. Make one small, policy-local improvement that makes the existing timing-protected score/retention decision suitable for a default-pipeline activation: preserve journal rollback, avoid threshold sweeps, and ensure only a positive leakage/power benefit with an acceptable timing budget is retained. This card must be paired with a minimal reachability activation in Optimizer only if required; do not modify Tcl. Emit METRIC|power_recovery_guarded_moves|<nonzero count> and METRIC|power_recovery_retained_gain|<nonzero gain> exclusively for committed retained moves, not trials.",
    ),
    MechanismCard(
        "repair_power_policy_final_guard",
        "repair_power_policy_guard",
        ("leakage", "power", "dynamic", "timing"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/Optimizer.cc"),
        ("repair_power_guarded_moves", "repair_power_retained_gain"),
        "Use the existing RepairPowerPolicy's journaled timing and leakage checks to implement one bounded post-setup power reclaim action reachable from the implicit `repair_timing -setup` pipeline. The mechanism must preserve the current LEGACY/LAST_GASP/CRIT_VT timing repairs, use a finite timing reserve, and retain only actual committed moves with measured local leakage benefit. Do not alter Tcl, external inputs, or custom phase behavior. Emit METRIC|repair_power_guarded_moves|<nonzero count> and METRIC|repair_power_retained_gain|<nonzero gain> only after a move survives the policy's final timing guard.",
    ),
    MechanismCard(
        "repair_power_explicit_timing_budget",
        "repair_power_timing_budget",
        ("tns", "timing", "timing_recovery", "power", "leakage", "rollback"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/policy/RepairPowerPolicy.cc"),
        ("repair_power_timing_rejected", "repair_power_timing_retained"),
        "The contest's default `repair_timing -setup` pipeline needs a bounded REPAIR_POWER experiment, not an unbounded reclaim tail. At the existing default-only Optimizer dispatch, make one existing REPAIR_POWER configuration reachable exactly once while leaving explicit custom phase lists untouched. Inspect the selected configuration's actual defaults and pass a finite, parent-reserve-derived timing budget and a finite move/window cap into RepairPowerPolicy; make that budget authoritative instead of widening it with the current unconditional `max(..., 1.0)` relaxation. Preserve journal rollback and retain only a window that remains within the measured timing budget and has positive leakage benefit. Emit METRIC|repair_power_timing_rejected|<nonzero count> only for rollbacks caused by this budget and METRIC|repair_power_timing_retained|<nonzero count> only for final committed moves. Do not modify Tcl, perform a threshold sweep, or silently retain an activation with no bounded acceptance proof.",
        refinement_signals=("repair_power_committed", "repair_power_leakage_gain"),
    ),
    MechanismCard(
        "repair_power_post_reclaim_timing_repair",
        "post_reclaim_timing_repair",
        ("tns", "timing", "timing_recovery", "power", "leakage", "critical_path"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/policy/SetupLastGaspPolicy.cc"),
        ("post_reclaim_timing_moves", "post_reclaim_timing_recovered"),
        "The promoted default pipeline performs LEGACY, LAST_GASP, CRIT_VT_SWAP, then the validated implicit late-leakage reclaim. Test one default-only post-reclaim timing-recovery phase using the already implemented LAST_GASP policy, without changing Tcl or any explicit custom phase list. It must run only after an implicit reclaim tail has actually been selected, retain the existing repair ordering otherwise, and emit METRIC|post_reclaim_timing_moves|<nonzero count> plus METRIC|post_reclaim_timing_recovered|<positive recovered timing amount> only when it commits repair moves. This is a causal recovery experiment: retain power only insofar as final official normalized distance improves.",
    ),
    MechanismCard(
        "repair_power_slack_frontier",
        "repair_power_slack_frontier",
        ("tns", "timing", "timing_recovery", "power", "leakage", "critical_path"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc",),
        ("repair_power_low_slack_rejected", "repair_power_safe_retained"),
        "At the validated RepairPowerPolicy late-leakage candidate selection boundary, refine the existing slack/criticality admission so a reclaim candidate is excluded before journaling when its local slack makes it likely to consume the promoted parent's remaining timing budget. Reuse the policy's measured timing and leakage data; do not add a global threshold sweep, edit Tcl, or disable the phase. Emit METRIC|repair_power_low_slack_rejected|<nonzero count> only for this admission guard and METRIC|repair_power_safe_retained|<nonzero count> only for final retained moves. Falsify the refinement if final TNS debt is not reduced without giving back more official power benefit than it protects.",
    ),
    MechanismCard(
        "repair_power_bounded_commit_window",
        "repair_power_commit_window",
        ("tns", "timing", "timing_recovery", "power", "leakage", "rollback"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc",),
        ("repair_power_window_rollbacks", "repair_power_window_retained"),
        "The validated late-leakage tail retained thousands of moves and improved power but left timing debt. At its existing window commit boundary, add one bounded rollback checkpoint that evaluates the whole committed window against the policy's baseline timing before accepting it. Keep the existing measured leakage requirement and journal semantics; reject the window rather than trying another arbitrary threshold. Emit METRIC|repair_power_window_rollbacks|<nonzero count> for reverted windows and METRIC|repair_power_window_retained|<nonzero count> for retained windows. Do not alter Tcl, benchmark data, or explicit phase schedules.",
    ),
    # R10 execution-derived expansion.  The inherited AES parent reaches the
    # late-leakage phase and accepts thousands of power-VT swaps, yet its
    # summary reports zero accepted size-down and buffer-removal moves.  This
    # is not another score/tie/window refinement: the post-route branch
    # unconditionally makes these candidate kinds unreachable before the
    # existing journaled timing guard can evaluate them.
    MechanismCard(
        "repair_power_postroute_sizedown_admission",
        "repair_power_postroute_sizedown_admission",
        ("leakage", "power", "timing", "sizing", "post_route"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc",),
        ("postroute_sizedown_examined", "postroute_sizedown_retained"),
        "The executed AES late_leakage_recovery phase reports thousands of accepted power-VT swaps but zero accepted size-down moves. At RepairPowerPolicy::generateLateLeakageCandidates, the post-route-sensitive early return makes size-down candidates unreachable before the existing per-candidate journal, timing update, full timing budget, legality, footprint, and zero-DRV checks can decide them. Make only a bounded admission of a legal positive-leakage/area size-down or size-down-plus-power-VT candidate whose target already meets the policy's existing min_unbuffer_slack condition; retain the existing post-route protected-cone exclusion and every existing commit guard. Do not change score weights, target order, timing limits, windows, phase scheduling, Tcl, or benchmark data. Emit METRIC|postroute_sizedown_examined|<positive count> only for candidates that pass the existing admission checks and reach the journaled evaluation, and METRIC|postroute_sizedown_retained|<positive count> only for final committed retained moves. If no such source-level candidate is reached, report that unactivation rather than broadening a threshold or relaxing a guard.",
    ),
    MechanismCard(
        "implicit_rmp_area_restructure",
        "implicit_rmp_area_restructure",
        ("leakage", "dynamic", "area", "timing", "logic_restructure"),
        ("src/rsz/src/Optimizer.cc", "src/rmp/src/Restructure.cpp"),
        ("rmp_area_attempted", "rmp_area_retained"),
        "The fixed contest flow never invokes the already built RMP combinational restructure engine, while the residual goal is dominated by leakage and dynamic power after all reachable cell-level RepairPower candidates have been exhausted. At a default-only Optimizer boundary distinct from REPAIR_POWER, make one bounded call to the existing RMP area-oriented restructure capability only after the normal timing-repair phases and only when its existing safety API can prove that the selected cone is combinational, legal, and has finite STA context. Reuse RMP's own snapshot/restore and acceptance machinery; retain a restructure only if its existing measured local result is non-worse in timing and improves area or power proxy, otherwise restore it. Do not modify Tcl, benchmark inputs, library files, phase schedules, global thresholds, or force a target by instance name. Emit METRIC|rmp_area_attempted|<positive count> only when the existing engine processes a legal cone, and METRIC|rmp_area_retained|<positive count> only for a non-restored accepted restructure. This is a source-level activation of an independent combinational optimization module, not a RepairPower refinement.",
        candidate_eligible=False,
    ),
    MechanismCard(
        "repair_power_rmp_area_recipe_v1",
        "repair_power_rmp_area_recipe",
        ("leakage", "dynamic", "power", "power_reclaim", "area", "logic_restructure", "rmp"),
        ("src/rmp/src/Restructure.cpp",),
        ("rmp_area_attempted", "rmp_area_retained"),
        "For the controller-owned rmp_area_power recipe only, repair_power first reconstructs the inherited low-power state and checkpoints it, then invokes one bounded `restructure -target area` pass with at most four tried endpoint clouds and at most one retained cloud. At Restructure's existing area-mode cloud admission, snapshot, ABC trial, and restore boundaries, make one source-level area/power-directed refinement: admit only a legal combinational cloud with finite STA context; emit METRIC|rmp_area_attempted|<positive> only when such a cloud enters a real ABC area trial; retain at most one result only when existing measured local area or leakage proxy strictly improves without worsening global TNS/WNS beyond the existing guards, otherwise restore it; emit METRIC|rmp_area_retained|<positive> only after non-restored acceptance. Preserve the controller budgets, generated merged Liberty, ABC modes, sequential-pin/HPWL/electrical/legal checks, repair_power implementation, public commands, benchmarks, and ordinary repair_timing. Do not invoke RMP from C++, hard-code a cone, force acceptance, relax a guard, or add an unbounded traversal. Falsify unless its exact no-diff rmp_area_power baseline is beaten in official post-route leakage/dynamic residual with TNS at or below the 200 ns Stage-1 ceiling, zero DRV, and official 4/4 LEC.",
        required_flow_commands=("repair_power", "restructure"),
        power_command_eligible=True,
        activation_signals=("rmp_area_attempted",),
    ),
    MechanismCard(
        "implicit_recover_power_default_activation",
        "recover_power_default_activation",
        ("leakage", "dynamic", "power", "downsize", "timing"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/RecoverPower.cc"),
        ("recover_power_attempted", "recover_power_committed"),
        "The fixed contest command enters Optimizer's default repair_timing pipeline but never invokes the separately implemented RecoverPower engine. At the default-only Optimizer completion boundary, after the normal timing-repair phases have finished, activate exactly one bounded existing RecoverPower pass over a small fixed fraction of eligible endpoints. Preserve its existing per-move journal restore, same-footprint/downsize eligibility, area/power-positive test, finite STA updates, and timing guard; leave explicit phase lists, Tcl, benchmarks, environment settings, and all RepairPower/RMP mechanisms unchanged. Emit METRIC|recover_power_attempted|<positive endpoint count> only after eligible endpoints enter the existing engine, and METRIC|recover_power_committed|<positive resize count> only for moves that survive its own journaled guard. This is a distinct source-level reachability experiment for RecoverPower, not a ranking or threshold refinement of RepairPower.",
    ),
    # R13 demonstrated that the independent RecoverPower engine is reached by
    # the default AES flow (14 evaluated endpoints), but its additional local
    # score retained no resize.  This is deliberately a guard-calibration
    # hypothesis rather than a revival of the activation card: it targets the
    # per-candidate full-design-area normalization that sits after a real
    # candidate is applied and before the existing journal is retained.
    MechanismCard(
        "recover_power_local_guard_unit_consistency",
        "recover_power_local_guard",
        ("leakage", "dynamic", "power", "downsize", "timing"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/RecoverPower.cc"),
        ("recover_power_guard_examined", "recover_power_guard_committed"),
        "R13 proved that the default-only RecoverPower activation reaches eligible AES endpoints but commits no move. At RecoverPower's already executed journaled acceptance boundary, replace only the full-design-area-normalized local area score that rejects a small legal downsize before its existing timing guard can retain it. The revised test must remain local and unit-consistent: require an actual positive area/power proxy reduction together with the existing finite TNS-expansion cap and WNS-damage cap; preserve same-footprint/downsize eligibility, the existing `better` timing predicate, journal restore, failed-move stop, explicit phase schedules, Tcl, benchmark data, and every RepairPower/RMP mechanism. Reintroduce the one bounded default-only invocation solely because R13 established that as the reachable engine entry, but do not change endpoint fraction or add a threshold sweep. Emit METRIC|recover_power_guard_examined|<positive count> only after a changed candidate reaches this revised guard, and METRIC|recover_power_guard_committed|<positive count> only after that candidate survives journal retention. Refute this guard family if it has no retained moves, if official post-route QoR lacks a strict normalized-distance gain, or if it violates the frozen post-route timing/DRV integrity contract.",
        refinement_signals=("recover_power_attempted",),
    ),
    # R14 validated the local acceptance rule: 12 of 14 examined endpoints
    # survived the existing journal and the complete QoR distance improved.
    # The remaining independent decision is coverage, which is fixed at 5%
    # in Optimizer rather than derived from observed timing headroom.  This is
    # one evidence-backed expansion of an integrated mechanism, not a sweep
    # of its acceptance guard.
    MechanismCard(
        "recover_power_endpoint_coverage_budget",
        "recover_power_endpoint_coverage",
        ("leakage", "dynamic", "power", "downsize", "timing"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/RecoverPower.cc"),
        ("recover_power_endpoint_budget", "recover_power_coverage_committed"),
        "R14 established that RecoverPower's unit-consistent local guard is active, journal-retained 12 of 14 changed candidates, and remains within the 11.999 ns contract. The fixed default-only invocation still covers only 5 percent of eligible endpoints, a reachability budget unrelated to the validated local acceptance rule. At that Optimizer-to-RecoverPower boundary, make exactly one bounded coverage experiment by replacing the literal 5 percent with one fixed 15 percent endpoint budget; do not sweep or adapt the percentage, and preserve the integrated local guard, endpoint ordering, eligibility, ranking, timing caps, WNS guard, journal restore, failed-move stop, explicit phase schedules, Tcl, benchmark inputs, RepairPower, and RMP. Emit METRIC|recover_power_endpoint_budget|<positive selected endpoint count> after the actual capped count is computed, and METRIC|recover_power_coverage_committed|<positive count> only for journal-retained moves made within this coverage experiment. Refute the card unless complete official post-route TNS, dynamic power, and leakage power strictly improve normalized distance below the fixed parent while TNS remains at or below 11.999 ns and all integrity gates pass.",
        refinement_signals=("recover_power_guard_examined", "recover_power_guard_committed"),
    ),
    # R15 disproved wider endpoint coverage: it activated the existing engine
    # but left all three official QoR metrics unchanged.  The next independent
    # decision is therefore not another coverage value, but which already
    # legal timing-equivalent replacement is selected inside downsizeCell.
    MechanismCard(
        "recover_power_leakage_frontier_selection",
        "recover_power_leakage_frontier",
        ("leakage", "dynamic", "power", "downsize", "timing"),
        ("src/rsz/src/RecoverPower.cc", "src/rsz/src/RecoverPower.hh"),
        ("recover_power_leakage_candidates", "recover_power_leakage_selected"),
        "R15 proved that increasing RecoverPower endpoint coverage from 5 to 15 percent creates additional journal-retained moves yet leaves complete official TNS, dynamic power, and leakage unchanged; do not change coverage again. At RecoverPower::downsizeCell, after the existing dont-use, footprint, drive, delay-margin, path-slack, and positive-power eligibility tests have admitted more than one candidate, select the candidate with the greatest measured static-leakage reduction only among candidates whose existing estimated delay is no worse than the current eligible incumbent. Preserve endpoint order, coverage, same-footprint eligibility, the unit-consistent local guard, TNS-expansion cap, WNS-damage cap, journal restore, failed-move stop, all phase schedules, Tcl, benchmark inputs, RepairPower, and RMP. Emit METRIC|recover_power_leakage_candidates|<positive actual comparison count> only for legal timing-equivalent candidates compared at this exact selection boundary, and METRIC|recover_power_leakage_selected|<positive count> only after a leakage-prioritized replacement survives the existing guard and journal retention. Do not modify global score weights or perform a threshold/coverage sweep. Refute unless zero-DRV integrity, official 4/4 LEC, complete post-route metrics, TNS at or below 11.999 ns, and a strict three-metric normalized-distance gain over the fixed parent all hold.",
        refinement_signals=("recover_power_guard_examined", "recover_power_guard_committed"),
    ),
    MechanismCard(
        "repair_power_goal_contract_cumulative_guard",
        "repair_power_goal_contract_guard",
        ("leakage", "dynamic", "power", "timing", "goal_contract"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_goal_cap_rejected", "repair_power_goal_cap_retained"),
        "The parent’s measured trajectory proves that the executed late_leakage_recovery phase lowers leakage from 89.4M pW to 68.1M pW and dynamic power from 408.9B pW to 387.9B pW before routing, while the frozen contract still permits final |TNS| up to 11.999 ns. The previous fixed ratio/window-budget trials are refuted and must not be recreated. The evaluator now exports only the frozen contract’s absolute TNS limit in `RSZ_GOAL_TNS_ABS_S`. At RepairPowerPolicy’s existing late per-move/window cumulative timing acceptance, consume that finite contract value only when present and finite: preserve every existing per-move WNS guard, positive measured leakage requirement, weighted local power-vs-timing guard, fanout check, journals, rollback, move classes, candidate ordering, quotas, and explicit phase behavior, but use the absolute contract limit as the cumulative late-reclaim ceiling instead of a parent-anchored ratio cap that is stricter than the contract. Never accept |TNS| above the supplied ceiling, do not change Tcl, source any other goal, tune percentages, alter max-move/target/window limits, or sweep a threshold. Emit `METRIC|repair_power_goal_cap_rejected|<positive>` only for moves/windows restored because they exceed this exact absolute ceiling, and `METRIC|repair_power_goal_cap_retained|<positive>` only for journal-retained late reclaim moves accepted through the goal-aware cumulative gate. Refute unless official post-route TNS, dynamic power, and leakage give a strict normalized-distance gain with zero DRV and official 4/4 LEC.",
        refinement_signals=("repair_power_guarded_moves", "repair_power_retained_gain"),
    ),
    MechanismCard(
        "repair_power_then_timing_recovery",
        "repair_power_recovery_sequence",
        ("leakage", "dynamic", "power", "timing", "phase_sequence"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/SetupLastGaspPolicy.cc"),
        ("pre_recovery_power_committed", "post_recovery_timing_moves"),
        "R1 showed that the existing early_forced_reclaim path can strongly reduce power but, when used as an unbounded post-repair tail, it leaves unusable timing; R3 showed that a post-reclaim timing pass after the already conservative late tail does not change final QoR. These are not evidence against a bounded reclaim-before-recovery sequence. In the default-only implicit pipeline, introduce exactly one bounded use of the existing early_forced_reclaim policy before the existing final timing-recovery work, then let the unmodified normal LAST_GASP/CRIT_VT timing recovery operate on that reclaimed design. Reuse the parent late phase’s existing 4200-move bound rather than adding a new quota, preserve all explicit phase lists, journals, legality/DRV checks, benchmark Tcl, candidate rules, and the current late tail; do not retune a timing cap, invoke an unbounded profile, or sweep ordering. Emit `METRIC|pre_recovery_power_committed|<positive>` only for journal-retained pre-recovery reclaim moves, and `METRIC|post_recovery_timing_moves|<positive>` only for timing-repair moves committed after that pre-recovery phase. The sequence is refuted unless official post-route TNS, dynamic power, and leakage strictly improve normalized distance with TNS at or below 11.999 ns and official 4/4 LEC.",
        refinement_signals=("repair_power_guarded_moves", "repair_power_retained_gain"),
    ),
    # R18 separated a real feasibility defect from the earlier timing-budget
    # experiments: its aggressive reclaim reached the power direction, but
    # retained 149 slew violations.  RepairPower's journal metrics currently
    # guard fanout, timing, and power proxies but do not make the existing
    # slew/cap legality boundary part of retention.  This is intentionally a
    # feasibility-preserving acceptance experiment, not a new phase, quota,
    # ranking, timing cap, or recovery-order retry.
    MechanismCard(
        "repair_power_electrical_feasibility_guard",
        "repair_power_electrical_feasibility",
        ("leakage", "dynamic", "power", "slew", "cap", "electrical_feasibility", "timing"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_electrical_rejected", "repair_power_electrical_retained"),
        "R18 proved that aggressive power-direction reclaim can reach substantial dynamic/leakage reduction, but it also retained 149 max-slew violations and therefore left an unrecoverable timing/electrical state. At RepairPowerPolicy's existing journaled candidate and window-acceptance boundary, extend the policy's already collected full-design feasibility metrics to include the existing STA max-slew and max-cap checks. When the baseline has zero such violations, restore a journal if an applied candidate or candidate window introduces either violation; otherwise preserve all existing timing, fanout, leakage, area, score, candidate-generation, ordering, phase, quota, and rollback behavior. Use existing STA legality queries rather than a new heuristic threshold, do not change Tcl, phase order, timing budgets, endpoint coverage, move limits, or candidate classes. Emit METRIC|repair_power_electrical_rejected|<positive> only for a journal restored because this new zero-baseline slew/cap check detected a newly introduced electrical violation, and METRIC|repair_power_electrical_retained|<positive> only for power-reclaim moves that survive that electrical check and the existing journal guard. Refute unless complete official post-route TNS, dynamic power, and leakage produce a strict normalized-distance gain below the fixed parent with zero DRV and official 4/4 LEC.",
        refinement_signals=("repair_power_guarded_moves", "repair_power_retained_gain"),
    ),
    # R8 trace-grounded expansion.  These cards deliberately target execution
    # boundaries observed in the inherited AES parent (rather than another
    # uninvoked policy or a retune of its rejected timing-window experiment).
    MechanismCard(
        "repair_power_pareto_candidate_order",
        "repair_power_pareto_order",
        ("leakage", "power", "timing", "candidate_ranking"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc",),
        ("pareto_power_candidates", "pareto_power_committed"),
        "At the existing late_leakage_recovery candidate-ranking boundary that is already reached by the implicit REPAIR_POWER phase, order legal candidates by a deterministic local Pareto relation: prefer a strictly larger measured leakage reduction only when it does not have a worse estimated timing cost than the incumbent. Preserve every existing legality check, journal rollback, and authoritative timing guard; do not change any timing budget, window size, move cap, phase list, Tcl, or environment setting. Emit METRIC|pareto_power_candidates|<positive examined count> after a real comparison and METRIC|pareto_power_committed|<positive retained count> only for final journal-retained moves. This is a distinct ordering experiment, not a retry of a budget or commit-window mechanism.",
    ),
    MechanismCard(
        "repair_power_move_class_frontier",
        "repair_power_move_class_frontier",
        ("leakage", "power", "timing", "vt_swap", "unbuffer"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc",),
        ("power_frontier_vt_moves", "power_frontier_unbuffer_moves"),
        "At the already executed RepairPower candidate frontier, preserve the existing score and timing guard but make the final choice class-aware: when a legal VT-swap/downsize and a legal unbuffer candidate have indistinguishable existing score ordering, choose the one with the larger measured static-leakage reduction. Do not manufacture candidates, alter move limits, or relax timing/DRV checks. Emit METRIC|power_frontier_vt_moves and METRIC|power_frontier_unbuffer_moves only for committed, journal-retained moves of their respective classes. Falsify if the flow presents no real cross-class choice; do not turn that into a threshold or cap sweep.",
    ),
    MechanismCard(
        "legacy_endpoint_journal_power_audit",
        "legacy_endpoint_journal_power_audit",
        ("leakage", "power", "timing", "journal"),
        ("src/rsz/src/policy/SetupLegacyBase.cc",),
        ("endpoint_power_journal_kept", "endpoint_power_journal_reverted"),
        "At SetupLegacyBase's executed endpoint-journal accept/restore boundary, compare the completed endpoint journal with its pre-journal design leakage using the existing timing-improvement decision as the primary condition. When two otherwise accepted endpoint states have equal existing timing outcome, retain the lower-leakage state; preserve the original timing-improvement requirement, journal semantics, repair sequence, and all limits. Emit METRIC|endpoint_power_journal_kept only for a committed lower-leakage endpoint journal and METRIC|endpoint_power_journal_reverted only for an actual restored higher-leakage tie. Do not add a slack threshold, modify phase order, or change Tcl.",
    ),
    MechanismCard(
        "setup_growth_pareto_gate",
        "setup_growth_pareto_gate",
        ("timing", "leakage", "dynamic", "sizeup", "vt_swap"),
        ("src/rsz/src/policy/SetupLegacyBase.cc",),
        ("setup_pareto_candidates", "setup_pareto_committed"),
        "At the executed SetupLegacyBase repair-candidate batch before a journaled endpoint update is committed, discard only a timing-growth candidate that is Pareto-dominated by another legal candidate in the same batch: the alternative must provide at least the same existing estimated timing improvement while adding no more static leakage and input capacitance. Preserve the normal timing-first behavior for all non-dominated candidates, endpoint order, journals, legal checks, and repair limits. Emit METRIC|setup_pareto_candidates|<positive compared count> after actual batch comparisons and METRIC|setup_pareto_committed|<positive committed count> only when a retained move was selected through this gate. Do not introduce a global power weight, new slack threshold, phase change, Tcl edit, or candidate sweep.",
    ),
    MechanismCard(
        "crit_vt_pareto_cell_selection",
        "crit_vt_pareto_selection",
        ("leakage", "power", "timing", "vt_swap"),
        ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc",),
        ("crit_vt_pareto_examined", "crit_vt_pareto_committed"),
        "At SetupCritVtSwapPolicy's already executed legal-VT-cell selection loop, retain the current timing-validity and max-cap tests, then select the lower-leakage cell only among candidates with the same existing timing category and equal-or-better delay estimate. Emit METRIC|crit_vt_pareto_examined after real equivalent candidates are compared and METRIC|crit_vt_pareto_committed only for an actual committed replacement. Do not alter the CRIT_VT phase schedule, global timing reserve, candidate count, or Tcl. This differs from the previously refuted telemetry-only VT experiment by changing the executed cell-selection decision itself.",
    ),
    MechanismCard(
        "legacy_setup_power_tiebreak",
        "legacy_setup_power_tiebreak",
        ("leakage", "power", "timing", "critical_path"),
        ("src/rsz/src/policy/SetupLegacyPolicy.cc",),
        ("legacy_power_tiebreak_kept", "legacy_power_tiebreak_rejected"),
        "At the executed SetupLegacyPolicy candidate-commit decision, use static leakage only as a deterministic tie-breaker between timing-equivalent legal moves. Preserve the existing primary timing ordering, all move generators, legality checks, and phase schedule. Emit METRIC|legacy_power_tiebreak_kept only when an otherwise timing-equivalent lower-leakage move is committed, and METRIC|legacy_power_tiebreak_rejected only when the tie-break keeps the existing timing choice. Do not introduce a slack threshold, a move-count cap, Tcl changes, or a sweep.",
    ),
    MechanismCard(
        "last_gasp_power_safe_repair",
        "last_gasp_power_safe_repair",
        ("leakage", "power", "timing", "critical_path"),
        ("src/rsz/src/policy/SetupLastGaspPolicy.cc",),
        ("last_gasp_power_safe_moves", "last_gasp_power_rejected"),
        "At the executed LAST_GASP endpoint-repair acceptance boundary, retain the existing timing-progress requirement and reject only a legal repair that gives no incremental timing progress while increasing the selected cell's static leakage. Emit METRIC|last_gasp_power_safe_moves for accepted timing-progress repairs that avoid that leakage regression and METRIC|last_gasp_power_rejected for this precise rejection. Do not alter endpoint limits, phase order, timing thresholds, Tcl, or benchmark inputs.",
    ),
    MechanismCard(
        "timing_recovery_crit_vt_worst_slack_frontier",
        "timing_recovery_crit_vt_frontier",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "vt_swap"),
        ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc",),
        ("timing_recovery_crit_vt_examined", "timing_recovery_crit_vt_retained"),
        "In the executed repair_timing CRIT_VT_SWAP policy, use the existing collected critical-instance slack and already computed legal candidate score to make one TNS-recovery ordering decision: visit the most-negative-slack legal candidates first, using the existing score only as a deterministic tie-breaker. Preserve candidate discovery, legalTimingVtCell, selectCritVtCell, the current maximum-committed-moves limit, journals, accept/restore behavior, timing/electrical checks, Tcl, and all repair_power behavior. Emit METRIC|timing_recovery_crit_vt_examined|<positive> only for an eligible candidate considered by this timing-first ordering, and METRIC|timing_recovery_crit_vt_retained|<positive> only after the existing committer accepts its VT swap. This is a direct repair_timing recovery experiment: it is refuted unless complete power_then_timing official post-route evidence strictly lowers TNS while dynamic and leakage remain within their targets, DRV stays zero, and official 4/4 LEC passes.",
    ),
    MechanismCard(
        "timing_recovery_last_gasp_global_tns_journal",
        "timing_recovery_last_gasp_journal",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "last_gasp", "rollback"),
        ("src/rsz/src/policy/SetupLastGaspPolicy.cc", "src/rsz/src/policy/SetupLastGaspPolicy.hh"),
        ("timing_recovery_last_gasp_examined", "timing_recovery_last_gasp_retained"),
        "In the executed repair_timing LAST_GASP journal-progress boundary, retain a bounded sequence only when its measured global TNS strictly improves from the phase baseline and its WNS does not fall below that same captured baseline. This permits a TNS-first recovery sequence that has a temporary endpoint-order trade-off, without weakening the final phase-level timing guard. Preserve the existing endpoint limits, move sequence, per-endpoint journals, max passes, legal/electrical checks, phase order, Tcl, and repair_power reconstruction. Emit METRIC|timing_recovery_last_gasp_examined|<positive> only after a real LAST_GASP progress decision is measured, and METRIC|timing_recovery_last_gasp_retained|<positive> only after the existing journal commits the sequence through this phase-baseline gate. Refute unless the complete power_then_timing official post-route result strictly lowers TNS while dynamic/leakage stay within target, DRV is zero, and official 4/4 LEC passes.",
    ),
    MechanismCard(
        "sizeup_power_cost_tiebreak",
        "sizeup_power_cost_tiebreak",
        ("leakage", "power", "timing", "sizing"),
        ("src/rsz/src/move/SizeUpGenerator.cc",),
        ("sizeup_power_tiebreak_kept", "sizeup_power_tiebreak_rejected"),
        "At the existing SizeUpGenerator equivalent-cell selection boundary, choose the lower-static-leakage legal cell only when its timing drive class and delay estimate are equivalent to the default selected cell. Preserve the primary setup-repair ordering and all non-equivalent choices. Emit METRIC|sizeup_power_tiebreak_kept for each committed tie-break choice and METRIC|sizeup_power_tiebreak_rejected when a higher-leakage cell remains necessary for a timing distinction. Do not add a drive threshold, cell sweep, Tcl change, or new repair phase.",
    ),
    MechanismCard(
        "buffer_power_cost_tiebreak",
        "buffer_power_cost_tiebreak",
        ("leakage", "power", "timing", "buffer"),
        ("src/rsz/src/move/BufferGenerator.cc",),
        ("buffer_power_tiebreak_kept", "buffer_power_tiebreak_rejected"),
        "At the existing BufferGenerator equivalent-buffer choice, prefer a lower-static-leakage legal buffer only when it preserves the selected timing and capacitance class. Keep mandatory electrical and timing repairs unchanged. Emit METRIC|buffer_power_tiebreak_kept only for such equivalent committed choices and METRIC|buffer_power_tiebreak_rejected only when the stronger buffer is required. Do not modify repair limits, routing, Tcl, or benchmark inputs.",
    ),
    MechanismCard(
        "path_frontier_selection",
        "path_selection",
        ("tns", "critical_path", "fanout"),
        ("src/rsz/src/RepairDesign.cc",),
        ("frontier_paths", "accepted_sizeup"),
        "Prioritize a bounded frontier of critical paths and stop expansion when marginal repair gain vanishes.",
    ),
    MechanismCard(
        "drv_repair_gate",
        "constraint_repair",
        ("drv", "slew", "cap"),
        ("src/rsz/src/RepairDesign.cc",),
        ("drv_fixed", "drv_rejected_timing"),
        "Gate electrical repair on a local timing reserve and record every rejected repair reason.",
    ),
    MechanismCard(
        "runtime_budget_guard",
        "runtime_guard",
        ("runtime", "tail", "convergence"),
        ("src/rsz/src/RepairDesign.cc",),
        ("iteration_gain", "early_stop"),
        "Stop a repair subpass when its observed distance gain per second falls below the protected budget.",
    ),
    MechanismCard(
        "route_critical_net_order",
        "route_ordering",
        ("route", "tns", "critical_path", "slack"),
        ("src/grt/src/fastroute/src/FastRoute.cpp",),
        ("critical_net_order", "critical_net_reordered"),
        "Use the executed FastRoute critical-net ordering boundary to prioritize only timing-critical nets, with bounded telemetry proving that ordering changed.",
    ),
    MechanismCard(
        "route_overflow_reroute",
        "route_congestion",
        ("route", "tns", "congestion", "overflow"),
        ("src/grt/src/GlobalRouter.cpp",),
        ("overflow_reroute", "reroute_net_count"),
        "At the executed global-routing overflow reroute boundary, prioritize a bounded set of timing-relevant dirty nets and record every applied reroute.",
    ),
    MechanismCard(
        "route_critical_percentage",
        "route_criticality",
        ("route", "tns", "critical_path", "timing"),
        ("src/grt/src/GlobalRouter.cpp",),
        ("critical_nets_fraction", "timing_aware_route"),
        "Use the existing global-router critical-net percentage mechanism only when timing data is available, and expose whether it changes the routed critical-net set.",
    ),
    MechanismCard(
        "route_multilib_timing_enable",
        "route_timing_enablement",
        ("route", "post_route", "tns", "liberty", "timing"),
        ("src/grt/src/GlobalRouter.cpp",),
        ("route_timing_enabled",),
        "At GlobalRouter::configFastRoute, calibrate the timing-availability guard for the contest's multi-Liberty STA setup: do not disable FastRoute critical-net ordering merely because defaultLibertyLibrary() is null when a usable Liberty library is actually present. Make one bounded, backward-safe C++ change, preserve the no-library fallback, and emit METRIC|route_timing_enabled|<positive critical-net percentage> only after the enabled policy is propagated to FastRoute. Do not modify Tcl or benchmark inputs.",
    ),
    MechanismCard(
        "route_sta_query_activation",
        "route_timing_activation",
        ("route", "post_route", "tns", "slack", "timing"),
        ("src/grt/src/GlobalRouter.cpp",),
        ("route_sta_timing_enabled",),
        "Activation repair for the multi-Liberty contest flow: the prior default-Liberty and library-iterator guards both failed despite measured STA timing. In GlobalRouter::configFastRoute, use one finite setup worst-slack query as the availability proof for FastRoute critical-net ordering; retain the conservative disable path when that query is unavailable or non-finite. Propagate only the existing bounded critical-net percentage and emit METRIC|route_sta_timing_enabled|<positive percentage> only when this STA-proven policy is enabled. Do not change Tcl, benchmark data, or global-route command options.",
    ),
    MechanismCard(
        "route_critical_fraction_calibration",
        "route_critical_fraction",
        ("route", "post_route", "tns", "critical_path", "timing"),
        ("src/grt/src/GlobalRouter.cpp", "src/grt/src/fastroute/src/FastRoute.cpp"),
        ("route_critical_fraction_calibrated",),
        "After an actually executed default critical-net fraction produced no timing movement, run one bounded calibration: keep the finite-STA availability gate, increase only FastRoute's built-in critical-net fraction to a modest fixed value (no more than 25 percent), and emit METRIC|route_critical_fraction_calibrated|<positive percentage> only after that value reaches the active FastRoute policy. Preserve the zero/no-timing fallback, do not alter Tcl, and make no unrelated routing-policy change.",
    ),
    MechanismCard(
        "route_resistance_aware_topology",
        "route_resistance_aware",
        ("route", "post_route", "tns", "parasitics", "resistance", "topology"),
        ("src/grt/src/GlobalRouter.cpp", "src/grt/src/fastroute/src/utility.cpp"),
        ("resistance_aware_nets",),
        "At the actual FastRoute resistance-aware routing boundary, enable the existing bounded resistance-aware strategy only when setup STA is finite, and repair its multi-Liberty availability guard using the same concrete timing evidence rather than defaultLibertyLibrary(). Emit METRIC|resistance_aware_nets|<positive count> only after the strategy marks a nonempty set of nets for resistance-aware routing. Preserve the no-timing fallback and existing percentage bound; do not alter Tcl, benchmark data, or the global-route command.",
    ),
    MechanismCard(
        "route_parasitic_handoff",
        "route_parasitics",
        ("route", "tns", "parasitics", "post_route"),
        ("src/grt/src/fastroute/src/FastRoute.cpp",),
        ("route_parasitics", "route_timing_handoff"),
        "At the executed FastRoute-to-parasitics handoff, apply a bounded timing-aware routing choice and prove that the handoff is reached before claiming QoR benefit.",
    ),
    # The inherited AES parent accepts thousands of leakage-improving VT swaps,
    # yet post-route dynamic power remains above target.  This is distinct from
    # prior score and timing-budget experiments: input capacitance is already
    # measured per candidate but is not a late-VT admission condition.
    MechanismCard(
        "repair_power_command_specialized_policy",
        "repair_power_command_specialized_policy",
        ("leakage", "dynamic", "power", "power_reclaim"),
        ("src/rsz/src/Resizer.tcl", "src/rsz/src/Resizer.i", "src/rsz/src/Resizer.cc", "src/rsz/src/Optimizer.cc", "src/rsz/src/policy/RepairPowerPolicy.cc"),
        ("repair_power_command_started", "repair_power_command_committed"),
        "The power stage executes the public `repair_power` command, which is parallel to—not an implicit phase of—`repair_timing`. Trace its Resizer.tcl/Resizer.i/Resizer::repairPower/Optimizer(REPAIR_POWER) path and make one small source-level improvement only in the power-specialized implementation it launches. If a timing-policy utility is needed, copy or adapt it behind the REPAIR_POWER path so ordinary `repair_timing` keeps its timing-oriented behavior. Preserve the command API and its phase/profile arguments, journals, legality checks, and the complete official flow. Emit METRIC|repair_power_command_started|<positive> only after the top-level power command reaches its policy, and METRIC|repair_power_command_committed|<positive> only after a power-path journal-retained move. Do not obtain power behavior by editing the normal repair_timing pipeline.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_early_forced_power_clone",
        "repair_power_early_forced_power_clone",
        ("leakage", "dynamic", "power", "power_reclaim", "vt_swap", "sizing"),
        ("src/rsz/src/Resizer.cc", "src/rsz/src/Optimizer.cc", "src/rsz/src/OptimizerTypes.hh", "src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_clone_examined", "repair_power_clone_retained"),
        "For the explicit repair_power -phase early_forced_reclaim run, trace the complete repair_power chain and keep a power-specialized copy/adaptation of any necessary candidate ranking, move selection, Setup/VT/size/buffer, parasitic, or routing-facing behavior behind REPAIR_POWER. The primary RepairPowerPolicy hook is an entry point, not a one-file limit. If a useful mechanism currently exists in a Setup* timing policy, clone/specialize it for the power path and invoke the copy only from repair_power rather than changing repair_timing's behavior in place. Retain only legal, journal-retained candidates with a measured power-direction benefit under the command's own timing/electrical guards. Emit METRIC|repair_power_clone_examined|<positive> for candidates reaching the cloned power-only decision and METRIC|repair_power_clone_retained|<positive> for retained commits. Do not change the public Tcl command or ordinary repair_timing semantics.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_early_electrical_safe_commit",
        "repair_power_early_electrical_safe_commit",
        ("leakage", "dynamic", "power", "power_reclaim", "slew", "cap", "fanout"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_electrical_rejected", "repair_power_electrical_retained"),
        "The explicit repair_power early_forced_reclaim command can achieve large dynamic/leakage reduction but its completed official flow showed nonzero DRV. At the power-only policy's existing journaled candidate/window acceptance boundary, add a bounded electrical-safe commit guard that detects a newly introduced slew/cap/fanout violation with the power policy's local/incremental STA evidence and restores that candidate or window. Reuse journals and the command's existing timing updates; do not modify ordinary repair_timing policies or add a full-design scan per candidate. Emit METRIC|repair_power_electrical_rejected|<positive> only for journal restores caused by this power-path electrical guard and METRIC|repair_power_electrical_retained|<positive> only for retained power moves that passed it. Refute unless official post-route DRV is zero and the leakage/dynamic frontier improves.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_early_tail_headroom",
        "repair_power_early_tail_headroom",
        ("leakage", "dynamic", "power", "power_reclaim", "timing_budget"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_tail_examined", "repair_power_tail_retained"),
        "R22 is a validated explicit repair_power result: it reached the early_forced_reclaim source cap of 12,500 retained moves (not candidate exhaustion), with zero official DRV, dynamic already below target, leakage still 40M pW versus the 35M pW goal, and only 4 terminal electrical rollbacks. At the existing early RepairPowerPolicy cap/continuation boundary, add one power-path-only tail decision that distinguishes cap exhaustion from candidate exhaustion. It may examine a bounded additional tail of previously unvisited legal candidates only while every existing journal, touched-instance, max-cap, local slew/cap/fanout, phase timing, and leakage-benefit guard remains active; retain a tail move only after it passes those existing guards. Do not modify Tcl, ordinary repair_timing, source a threshold sweep, or remove/relax the existing electrical guard. Emit METRIC|repair_power_tail_examined|<positive> only for tail candidates that actually reach the existing journaled decision, and METRIC|repair_power_tail_retained|<positive> only for final retained tail commits. Refute unless post-route official dynamic and leakage strictly improve with zero DRV and official 4/4 LEC, while the Stage-1 TNS safety ceiling remains satisfied.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_tail_leakage_first_frontier",
        "repair_power_tail_leakage_first_frontier",
        ("leakage", "power", "power_reclaim", "tail", "candidate_order"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_tail_leakage_examined", "repair_power_tail_leakage_retained"),
        "R23 validated the bounded repair_power tail with zero DRV and official leakage improvement, but its 256 retained tail moves were predominantly area-driven size-downs with tiny measured leakage gain (for example about 3.85e-11), while the unresolved target is leakage. At the already active RepairPowerPolicy early-tail ordering boundary only, introduce a power-specialized lexicographic ranking that prefers larger positive measured leakage gain before the existing input-cap/area tie breakers. Apply it only after the default 12,500-move prefix has completed; preserve candidate generation, all existing move classes, quotas, journals, local electrical guard, timing guard, touched-instance behavior, and ordinary repair_timing semantics. Do not tune numeric weights, source a threshold sweep, or relax any guard. Emit METRIC|repair_power_tail_leakage_examined|<positive> only for tail candidates reaching this leakage-first order, and METRIC|repair_power_tail_leakage_retained|<positive> only for final journal-retained tail commits selected through it. Refute unless official post-route leakage strictly improves from the current parent with dynamic at or below its target, zero DRV, and official 4/4 LEC.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_early_vt_coverage_frontier",
        "repair_power_early_vt_coverage",
        ("leakage", "power", "power_reclaim", "vt_swap", "candidate_coverage"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_early_vt_examined", "repair_power_early_vt_retained"),
        "The explicit repair_power early_forced_reclaim path calls RepairPowerPolicy::generateCandidates, where VT-equivalent cells are admitted before the fixed per-target candidate truncation. The current parent has reached its early cap and still misses leakage, while tail reordering did not change final QoR. At this active early candidate-construction boundary, preserve the same candidate count, move classes, eligibility tests, quota, score, journals, timing/electrical guards, and ordinary repair_timing behavior, but make the bounded VT-equivalent subset power-specialized: before the existing truncation, retain strict power-direction positive-leakage VT alternatives so the existing early policy can actually consider the best such alternative for that target rather than losing it to iteration order. Emit METRIC|repair_power_early_vt_examined|<positive> only when a strict power-direction VT option reaches this bounded early selection, and METRIC|repair_power_early_vt_retained|<positive> only after the existing early journal commits one. Do not enlarge the cap, add candidates, tune weights or thresholds, alter public Tcl, or weaken any guard.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_explicit_late_second_pass",
        "repair_power_explicit_late_second_pass",
        ("leakage", "power", "power_reclaim", "vt_swap", "command_dispatch"),
        ("src/rsz/src/Resizer.cc", "src/rsz/src/Optimizer.cc", "src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_late_second_pass_started", "repair_power_late_second_pass_retained"),
        "The public repair_power command currently configures only REPAIR_POWER with early_forced_reclaim; its already implemented late_leakage_recovery policy is otherwise reachable only from the ordinary implicit repair_timing path. Create one dedicated REPAIR_POWER-only second pass inside the explicit repair_power command path: after the successful early_forced_reclaim run, invoke the existing late-leakage policy through a separate power-specialized Optimizer configuration, preserving its existing target collection, candidate construction, journal rollback, max-cap, timing/electrical checks, and finite default limits. Do not alter repair_timing, its implicit phases, public Tcl API, benchmarks, or use an unbounded loop. Emit METRIC|repair_power_late_second_pass_started|<positive> only when that explicit power-only late pass begins, and METRIC|repair_power_late_second_pass_retained|<positive> only when its existing journal retains a move. Refute unless official post-route leakage strictly improves while dynamic remains at or below its target, TNS remains within the Stage-1 ceiling, DRV is zero, and official 4/4 LEC passes.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_stage_budgeted_late_guard",
        "repair_power_stage_budgeted_late_guard",
        ("leakage", "dynamic", "power", "power_reclaim", "timing_budget", "goal_contract"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_stage_budget_examined", "repair_power_stage_budget_retained"),
        "The matched JPEG power-only baseline proves that explicit repair_power reaches late_leakage_recovery, where 4,200 moves are retained but 1,344 otherwise evaluated candidates are rejected by the parent-relative late_timing_budget while post-route TNS is 104.08 ns and the authoritative Stage-1 ceiling is 200 ns. At the existing late per-candidate and window journal acceptance boundary, consume the controller-exported RSZ_POWER_STAGE_TNS_CEILING_S only when it is finite and positive. Preserve every local WNS, slew, capacitance, fanout, positive-leakage, candidate-order, quota, journal, rollback, and ordinary repair_timing rule, but replace only the stricter cumulative parent-relative TNS rejection with the absolute Stage-1 ceiling for the explicit REPAIR_POWER path. Never accept measured absolute TNS above that ceiling and do not change public Tcl, numeric caps, target coverage, or ordinary repair_timing. Emit METRIC|repair_power_stage_budget_examined|<positive> only for a late candidate/window that reaches this new absolute cumulative guard and METRIC|repair_power_stage_budget_retained|<positive> only after its journal is retained. Refute unless final official leakage drops below the matched parent while dynamic remains at or below 250 mW, TNS remains at or below the exported ceiling, DRV is zero, and official 4/4 LEC passes.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_stage_budgeted_late_tail",
        "repair_power_stage_budgeted_late_tail",
        ("leakage", "dynamic", "power", "power_reclaim", "late_tail", "move_cap"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_late_tail_examined", "repair_power_late_tail_retained"),
        "The matched JPEG power-only baseline reaches the exact late_leakage_recovery cap of 4,200 retained moves, still misses official leakage by 30 uW, and remains below the 200 ns Stage-1 TNS ceiling. Add one bounded explicit-REPAIR_POWER late continuation that runs only when the existing late move cap, rather than candidate exhaustion, ended the pass. Examine at most 512 previously unvisited candidates and retain at most 256; preserve late candidate generation and ordering, touched-instance state, positive measured leakage, local WNS/electrical guards, the absolute RSZ_POWER_STAGE_TNS_CEILING_S cumulative guard, journals, rollback, and ordinary repair_timing semantics. Do not alter the public command, default 4,200-move prefix, target cap, or use an unbounded revisit. Emit METRIC|repair_power_late_tail_examined|1 for each continuation candidate reaching the existing journaled decision and METRIC|repair_power_late_tail_retained|1 only for a retained continuation move. Refute unless official leakage strictly improves with dynamic at or below 250 mW, TNS at or below the stage ceiling, zero DRV, and official 4/4 LEC.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_goal_budgeted_critical_halo",
        "repair_power_goal_budgeted_critical_halo",
        ("leakage", "dynamic", "power", "power_reclaim", "vt_swap", "critical_halo"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_goal_halo_examined", "repair_power_goal_halo_retained"),
        "The matched JPEG power-only log shows that the inherited early policy hard-excludes 1,459 pure-VT halo candidates plus 505 compound-halo and 618 compound-fanin-halo candidates, even though final TNS is 104.08 ns against the explicit Stage-1 ceiling of 200 ns. At the existing critical-halo exclusion and journal boundary, preserve the halo as the default fast rejection, but allow a bounded REPAIR_POWER-only reconsideration of an otherwise legal strict positive-leakage VT candidate when RSZ_POWER_STAGE_TNS_CEILING_S is finite. Keep the existing move and trial caps, candidate classes, ranking, WNS/electrical checks, touched-instance rules, journals, rollback, and ordinary repair_timing unchanged; retain a reconsidered candidate only after the measured cumulative absolute TNS remains within the stage ceiling. Do not globally disable critical-cone protection or change Tcl. Emit METRIC|repair_power_goal_halo_examined|<positive> only for a formerly halo-excluded candidate reaching this journaled measured-budget check and METRIC|repair_power_goal_halo_retained|<positive> only after retention. Refute unless final official leakage strictly improves with dynamic at or below 250 mW, TNS at or below the ceiling, zero DRV, and official 4/4 LEC.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_late_tail_second_tranche",
        "repair_power_late_tail_second_tranche",
        ("leakage", "dynamic", "power", "power_reclaim", "late_tail", "refine"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_late_tail2_examined", "repair_power_late_tail2_retained"),
        "The current parent already contains the validated first bounded late continuation: R4 examined 464 candidates, hit its exact 256-retained cap, and reduced official leakage from 110 uW to 108 uW with TNS 104.43 ns. Preserve that successful prefix unchanged. Add exactly one second bounded tranche after the inherited first tranche reaches its retained cap: examine at most 512 additional previously unvisited late candidates and retain at most 256 additional moves. Preserve candidate ordering, touched-instance continuity, positive measured leakage, local WNS/electrical checks, RSZ_POWER_STAGE_TNS_CEILING_S cumulative guard, journals, rollback, the original 4,200-move prefix, the first tranche, and ordinary repair_timing semantics. Emit METRIC|repair_power_late_tail2_examined|1 only for candidates in the second tranche and METRIC|repair_power_late_tail2_retained|1 only after a second-tranche journal is retained. Do not reset touched state, replay the first tranche, change public Tcl, or create an unbounded loop. Refute unless official leakage strictly improves below 108 uW with dynamic at or below 250 mW, TNS at or below the stage ceiling, zero DRV, and official 4/4 LEC.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_goal_bounded_deep_late_continuation",
        "repair_power_goal_bounded_deep_late_continuation",
        ("leakage", "dynamic", "power", "power_reclaim", "late_tail", "goal_bounded"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_deep_tail_batches", "repair_power_deep_tail_retained"),
        "The current parent contains two validated late continuation tranches after the original 4,200-move late prefix: the first reduced official leakage from 110 to 108 uW and the second to 107 uW, while TNS remains only 105.31 ns against the 200 ns Stage-1 ceiling. Replace no successful prefix. After both inherited tranches finish, add one bounded goal-directed continuation of at most eight batches, each examining at most 1,024 previously unvisited candidates and retaining at most 512 moves, with a total additional retained cap of 4,096. Preserve candidate ordering and touched-instance continuity across all batches. After every batch, use the existing full metrics/journal machinery and stop on any batch with no positive measured leakage reduction, any WNS/electrical failure, measured cumulative TNS above RSZ_POWER_STAGE_TNS_CEILING_S, candidate exhaustion, or the total cap; rollback the failing batch. Preserve positive-leakage admission, all existing local guards, public Tcl, the 4,200 prefix, both inherited tranches, and ordinary repair_timing semantics. Emit METRIC|repair_power_deep_tail_batches|1 only for each completed positive-leakage batch and METRIC|repair_power_deep_tail_retained|1 only for retained moves in this deep continuation. Do not reset touched state, replay earlier candidates, tune a score threshold, or create an unbounded loop. Refute unless official leakage strictly improves below 107 uW with dynamic at or below 250 mW, TNS at or below 200 ns, zero DRV, and official 4/4 LEC.",
        candidate_eligible=False,
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_deep_late_microbatch_continuation",
        "repair_power_deep_late_microbatch_continuation",
        ("leakage", "dynamic", "power", "power_reclaim", "late_tail", "journal", "microbatch"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        (
            "repair_power_deep_microbatches_examined",
            "repair_power_deep_microbatch_retained",
            "repair_power_deep_microbatch_rolled_back",
        ),
        "R6 started from the validated R5 two-tranche parent and proved that one atomic 512-move deep-tail batch reaches the real REPAIR_POWER path but is wholly restored by the existing timing guard; its official endpoint therefore stayed at 107 uW leakage. Starting from the same retained parent, preserve the 4,200-move prefix and both validated continuation tranches exactly, then implement one bounded deep continuation over at most 512 additional previously unvisited, existing-score-ordered candidates. Partition them into consecutive fixed microbatches of at most 32 moves. Measure each microbatch with the existing full-metrics journal, positive measured-leakage, WNS, electrical, weighted timing-power, and absolute 200 ns Stage-1 TNS guards. Retain a passing microbatch, restore a failing one, mark its attempted instances touched, and continue to the next microbatch; do not let one rejected microbatch suppress all later candidates. Keep candidate generation and ordering, public Tcl, ordinary repair_timing, and every successful inherited policy unchanged. Do not add recursion, a threshold sweep, a second candidate ranking, or exceed 16 attempted microbatches/512 examined candidates/512 retained moves. Emit METRIC|repair_power_deep_microbatches_examined|1 only after a real microbatch enters its journal; emit METRIC|repair_power_deep_microbatch_retained|<positive move count> only after journal retention; and emit METRIC|repair_power_deep_microbatch_rolled_back|<positive move count> only after an actual restore. Refute unless official leakage strictly improves below 107 uW with dynamic at or below 250 mW, TNS at or below 200 ns, zero DRV, and official 4/4 LEC.",
        candidate_eligible=False,
        refinement_signals=("repair_power_deep_tail_batches",),
        power_command_eligible=True,
        activation_signals=("repair_power_deep_microbatches_examined",),
    ),
    MechanismCard(
        "repair_power_deep_late_eight_move_windows",
        "repair_power_deep_late_eight_move_windows",
        ("leakage", "dynamic", "power", "power_reclaim", "late_tail", "journal", "eight_move_window"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        (
            "repair_power_deep8_windows_examined",
            "repair_power_deep8_moves_retained",
            "repair_power_deep8_moves_rolled_back",
        ),
        "The interrupted JPEG R7 experiment conclusively activated sixteen consecutive 32-move deep microbatches after the validated R5 prefix and two continuation tranches; all sixteen were restored specifically by late_microbatch_timing_budget and official post-route QoR remained exactly 105.31 ns TNS, 242.893 mW dynamic, and 107 uW leakage. This is a batch-granularity refutation, not missing telemetry. Starting from the retained R5 parent, preserve the 4,200-move prefix and both validated tranches exactly. After tranche two terminates by either its 256-retained cap or its 512-examined cap, process at most 512 additional previously unvisited existing-score-ordered candidates in consecutive windows of at most eight moves. Give every window its own existing full-metrics journal; retain it only with positive measured leakage reduction and all existing WNS, TNS, weighted timing-power, fanout, max-cap, electrical, and absolute 200 ns Stage-1 guards. Restore a failing window, mark only its attempted instances touched, and continue to the next window. Preserve candidate generation/order, ordinary repair_timing, public Tcl, and all inherited successful behavior. Do not retest 32-move windows, introduce recursion or a score/threshold sweep, or exceed 64 windows/512 examined candidates/512 retained moves. Emit METRIC|repair_power_deep8_windows_examined|1 only after a real eight-or-fewer-move window enters its journal; emit METRIC|repair_power_deep8_moves_retained|<positive move count> only after retention; and emit METRIC|repair_power_deep8_moves_rolled_back|<positive move count> only after an actual restore. Refute unless official leakage strictly improves below 107 uW with dynamic at or below 250 mW, TNS at or below 200 ns, zero DRV, and official 4/4 LEC.",
        candidate_eligible=False,
        refinement_signals=("repair_power_deep_microbatches_examined",),
        power_command_eligible=True,
        activation_signals=("repair_power_deep8_windows_examined",),
    ),
    MechanismCard(
        "repair_power_deep_late_four_move_windows",
        "repair_power_deep_late_four_move_windows",
        ("leakage", "dynamic", "power", "power_reclaim", "late_tail", "journal", "four_move_window"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        (
            "repair_power_deep4_windows_examined",
            "repair_power_deep4_moves_retained",
            "repair_power_deep4_moves_rolled_back",
        ),
        "JPEG R7 conclusively tested 64 consecutive eight-or-fewer-move deep windows after the validated R5 prefix and two tranches. Four windows retained 32 additional power-VT moves and lowered the late-policy internal leakage proxy from about 66.7273 to 66.5711 uW, while 461 moves were hidden inside windows restored by the existing late timing guard; official post-route leakage remained quantized at 107 uW. Preserve every inherited successful prefix and admission rule, but replace only that already tested deep window granularity with consecutive windows of at most four existing-score-ordered, previously unvisited candidates after tranche two completes by retained or examined cap. Test at most 512 candidates/128 windows/512 retained moves. Each window must use the existing full-metrics journal and retain only with positive measured leakage plus all existing max-cap, electrical, WNS, TNS, weighted timing-power, fanout, and absolute 200 ns Stage-1 guards; restore a failing window, mark its attempted instances touched, and continue. Do not re-run 8/32/512-move windows, add recursion, alter candidate generation/order or thresholds, change public Tcl, or modify ordinary repair_timing. Emit METRIC|repair_power_deep4_windows_examined|1 only after a real window enters its journal; emit METRIC|repair_power_deep4_moves_retained|<positive move count> only after retention; and emit METRIC|repair_power_deep4_moves_rolled_back|<positive move count> only after restore. Refute unless official leakage falls below 107 uW with dynamic at or below 250 mW, TNS at or below 200 ns, zero DRV, and official 4/4 LEC.",
        refinement_signals=("repair_power_deep8_windows_examined", "repair_power_deep8_moves_retained"),
        power_command_eligible=True,
        activation_signals=("repair_power_deep4_windows_examined",),
    ),
    MechanismCard(
        "repair_power_integrate_validated_halo_after_tail",
        "repair_power_integrate_validated_halo_after_tail",
        ("leakage", "dynamic", "power", "power_reclaim", "critical_halo", "integrate"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_integrated_halo_examined", "repair_power_integrated_halo_retained"),
        "The current parent contains R4 Student 1's validated bounded late tail (108 uW leakage, 242.892 mW dynamic, 104.43 ns TNS). Its R4 sibling independently validated the bounded critical-halo reconsideration (1,563 examined/retained, 108 uW leakage, 240.892 mW dynamic, 121.05 ns TNS) but that sibling was not the promoted source. Perform one source-grounded incremental integration experiment: preserve the inherited late-tail implementation exactly and add the sibling's REPAIR_POWER-only critical-halo reconsideration at its original exclusion/journal boundary. Retain a formerly halo-excluded strict positive-leakage VT candidate only after existing WNS/electrical checks and measured cumulative TNS within RSZ_POWER_STAGE_TNS_CEILING_S; preserve all move/trial caps, ordering, journals, rollback, and ordinary repair_timing. Emit METRIC|repair_power_integrated_halo_examined|<positive> only for the integrated halo branch and METRIC|repair_power_integrated_halo_retained|<positive> only after retention. Do not assume the sibling gains are additive or alter the inherited late tail. Refute unless it strictly improves the matched parent leakage/dynamic residual vector with dynamic at or below 250 mW, TNS at or below 200 ns, zero DRV, and official 4/4 LEC.",
        power_command_eligible=True,
    ),
    MechanismCard(
        "repair_power_late_vt_cap_safe_frontier",
        "repair_power_late_vt_cap_frontier",
        ("dynamic", "leakage", "power", "input_cap", "timing"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc",),
        ("late_cap_safe_vt_examined", "late_cap_safe_vt_retained"),
        "At RepairPowerPolicy::generateLateLeakageCandidates, the active AES late-leakage tail accepts a strict leakage-direction VT swap even when its already computed input-capacitance proxy is negative, which can trade a static-leakage win for post-route dynamic-power regression. Add one bounded admission frontier only for the existing late power-VT-swap candidates: retain candidates with positive leakage gain and nonnegative input-cap gain, then preserve target order, protected timing cone, quotas, score, journals, timing updates, full timing/DRV checks, phase sequence, Tcl, and all other move classes. Emit METRIC|late_cap_safe_vt_examined|<positive count> only for late VT candidates reaching this new admission check and METRIC|late_cap_safe_vt_retained|<positive count> only for final journal-retained moves admitted by it. Do not tune a threshold or broaden candidate coverage. Refute unless final official post-route normalized QoR strictly improves with zero DRV and official 4/4 LEC.",
    ),
    MechanismCard(
        "repair_power_late_vt_cap_safe_isolated_revalidation",
        "repair_power_late_vt_cap_isolated",
        ("leakage", "dynamic", "power", "input_cap"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc",),
        ("late_cap_safe_vt_examined", "late_cap_safe_vt_retained"),
        "The previous cap-safe late-VT attempt emitted real REPAIR_POWER telemetry but was confounded by an out-of-scope SetupCritVtSwapPolicy edit, so it is not valid evidence for the power mechanism. Re-test exactly the existing RepairPowerPolicy admission condition in isolation: at the existing late power-VT candidate construction, require positive measured leakage gain and nonnegative already-computed input-cap gain; emit the two existing late_cap_safe telemetry counters only from this policy's examined and journal-retained candidates. Change no Setup*, Optimizer, PowerRecoveryPlus, routing, Tcl, quota, score, target order, timing guard, journal, or any other source file. This is a source-scope revalidation, not a threshold sweep. Refute unless the full official post-route flow improves the active leakage/dynamic power frontier with zero DRV and official 4/4 LEC.",
        refinement_signals=("late_cap_safe_vt_examined", "late_cap_safe_vt_retained"),
    ),
    # The earlier POWER_RECOVERY_PLUS activation used a different reclaim
    # profile and did not produce a usable final-QoR gain.  This card is an
    # independent late-profile activation that reuses its own bounded policy
    # and existing per-candidate journals rather than revisiting that attempt.
    MechanismCard(
        "implicit_power_recovery_plus_late_profile",
        "power_recovery_plus_late_profile",
        ("dynamic", "leakage", "power", "buffer_removal", "timing"),
        ("src/rsz/src/Optimizer.cc", "src/rsz/src/policy/PowerRecoveryPlusPolicy.cc"),
        ("power_recovery_plus_late_examined", "power_recovery_plus_late_retained"),
        "The fixed contest flow reaches Optimizer's implicit default pipeline, while the separate PowerRecoveryPlusPolicy is built but not part of that default sequence. After the unmodified implicit timing and RepairPower phases, activate exactly one bounded default-only POWER_RECOVERY_PLUS late_leakage_recovery pass using its existing candidate generation, per-candidate journal restore, finite timing updates, buffer-removal fanout guard, and convergence limit. Configure the policy through its existing profile/configuration boundary; do not edit Tcl, benchmarks, explicit phase schedules, RepairPower, RMP, global limits, or retry the prior early_forced_reclaim activation. Emit METRIC|power_recovery_plus_late_examined|<positive count> only after a real late-profile candidate reaches policy measurement and METRIC|power_recovery_plus_late_retained|<positive count> only after a journal-retained commit. Refute unless full official post-route QoR strictly improves with zero DRV and official 4/4 LEC.",
    ),
    # R27 begins an explicit timing-recovery campaign from the R26 low-power
    # parent.  These are deliberately independent policy decisions, not a
    # revival of the earlier default LAST_GASP/CRIT_VT cards which have final
    # negative evidence under a different schedule.
    MechanismCard(
        "timing_recovery_tns_endpoint_progress",
        "timing_recovery_tns_endpoint_progress",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_probe", "endpoint_order"),
        ("src/rsz/src/policy/SetupTnsPolicy.cc", "src/rsz/src/policy/SetupTnsPolicy.hh"),
        ("timing_tns_endpoint_examined", "timing_tns_endpoint_retained"),
        "In the executed TNS repair_timing policy, preserve legal endpoint collection, move generation, journals, electrical checks, and all Tcl parameters, but make one bounded global-progress decision: after a legal endpoint repair is measured, retain it only when the policy's existing current global TNS strictly improves or the same endpoint's slack improves without worsening the captured phase WNS. Keep the existing repair_tns cap and phase sequence; do not introduce endpoint names, a threshold sweep, or power-policy changes. Emit METRIC|timing_tns_endpoint_examined|<positive> only for measured legal endpoint repairs and METRIC|timing_tns_endpoint_retained|<positive> only for journal-retained repairs. This explores TNS-phase commitment, not the previously refuted LAST_GASP rule.",
    ),
    MechanismCard(
        "timing_recovery_wns_cone_shared_progress",
        "timing_recovery_wns_cone_shared_progress",
        ("tns", "wns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_probe", "cone"),
        ("src/rsz/src/policy/SetupWnsPolicy.cc", "src/rsz/src/policy/SetupWnsPolicy.hh"),
        ("timing_wns_cone_examined", "timing_wns_cone_retained"),
        "For the controller-selected WNS_CONE recipe only, at SetupWnsPolicy's existing cone repair journal boundary, prefer a legal repair when it improves the phase's aggregate negative slack across the collected cone and does not worsen the captured worst slack. Preserve cone collection, endpoint discovery, candidate/move ordering, all existing legal/electrical guards, journals, repair_tns, passes, and Tcl. Do not substitute manually named endpoints or widen the cone. Emit METRIC|timing_wns_cone_examined|<positive> after a legal cone repair is measured and METRIC|timing_wns_cone_retained|<positive> only after journal retention.",
    ),
    MechanismCard(
        "timing_recovery_legacy_mt_convergence",
        "timing_recovery_legacy_mt_convergence",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_probe", "legacy_mt"),
        ("src/rsz/src/policy/SetupLegacyMtPolicy.cc", "src/rsz/src/policy/SetupLegacyMtPolicy.hh"),
        ("timing_legacy_mt_batches", "timing_legacy_mt_retained"),
        "For the controller-selected LEGACY_MT recipe, at SetupLegacyMtPolicy's existing batch-commit/convergence boundary, use the already measured batch TNS delta to retain one bounded batch only when global TNS improves from the batch baseline and no new electrical violation appears. Preserve thread partitioning, endpoint enumeration, move types, journals, legalization, phase list, and Tcl; do not add a parallelism sweep. Emit METRIC|timing_legacy_mt_batches|<positive> only for measured batches and METRIC|timing_legacy_mt_retained|<positive> only for retained batches.",
    ),
    MechanismCard(
        "timing_recovery_reroute_net_delay_commit",
        "timing_recovery_reroute_net_delay_commit",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_probe", "reroute", "post_route"),
        ("src/rsz/src/policy/SetupReroutePolicy.cc", "src/rsz/src/policy/SetupReroutePolicy.hh"),
        ("timing_reroute_net_examined", "timing_reroute_net_retained"),
        "For the controller-selected REROUTE recipe only, refine SetupReroutePolicy's existing resistance-aware candidate commit: retain a legal reroute candidate only when its measured critical-net delay improves and the policy's current global TNS does not worsen from the phase baseline. Preserve candidate discovery, congestion/electrical checks, journals, fallback timing moves, global-route Tcl boundary, and all repair_power behavior. Emit METRIC|timing_reroute_net_examined|<positive> for measured legal critical-net candidates and METRIC|timing_reroute_net_retained|<positive> only after the existing commit succeeds.",
    ),
    MechanismCard(
        "timing_recovery_legacy_deep_batch_progress",
        "timing_recovery_legacy_deep_batch_progress",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "legacy"),
        ("src/rsz/src/policy/SetupLegacyPolicy.cc", "src/rsz/src/policy/SetupLegacyPolicy.hh"),
        ("timing_legacy_deep_examined", "timing_legacy_deep_committed", "timing_legacy_deep_journal_rollbacks", "timing_legacy_deep_retained"),
        "For the controller-selected two-pass LEGACY recipe only, refine SetupLegacyPolicy at its existing legal move/journal boundary so that a measured candidate is retained when it strictly improves current global TNS without introducing a new electrical violation. Keep endpoint collection, move sequence, repair-power reconstruction, Tcl recipe, legalization, and existing rollback semantics intact. Emit cumulative METRIC counters for timing_legacy_deep_examined (legal candidates), timing_legacy_deep_committed (successful journal commits), timing_legacy_deep_journal_rollbacks (including explicit zero), and timing_legacy_deep_retained (final retained commits).",
    ),
    MechanismCard(
        "timing_recovery_last_gasp_deep_progress",
        "timing_recovery_last_gasp_deep_progress",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "last_gasp"),
        ("src/rsz/src/policy/SetupLastGaspPolicy.cc", "src/rsz/src/policy/SetupLastGaspPolicy.hh"),
        ("timing_last_gasp_deep_examined", "timing_last_gasp_deep_committed", "timing_last_gasp_deep_journal_rollbacks", "timing_last_gasp_deep_retained"),
        "For the controller-selected two-pass LAST_GASP recipe only, make one source-local measured commit decision in SetupLastGaspPolicy: retain a legal late-stage repair only when global TNS improves and the worst slack does not degrade from the captured pass value. Preserve candidate discovery, legal/electrical checks, journals, sequence, Tcl recipe, and repair-power semantics. Emit cumulative METRIC counters for timing_last_gasp_deep_examined, timing_last_gasp_deep_committed, timing_last_gasp_deep_journal_rollbacks (including explicit zero), and timing_last_gasp_deep_retained.",
    ),
    MechanismCard(
        "timing_recovery_measured_vt_deep_progress",
        "timing_recovery_measured_vt_deep_progress",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "measured_vt"),
        ("src/rsz/src/policy/MeasuredVtSwapPolicy.cc", "src/rsz/src/policy/MeasuredVtSwapPolicy.hh"),
        ("timing_measured_vt_examined", "timing_measured_vt_committed", "timing_measured_vt_journal_rollbacks", "timing_measured_vt_retained"),
        "For the controller-selected two-pass MEASURED_VT_SWAP recipe only, refine MeasuredVtSwapPolicy at its existing post-measurement commit boundary: retain a legal VT swap only if measured global TNS improves and it does not worsen the captured phase WNS. Preserve candidate ranking, legal/electrical checks, journals, the controller recipe, and all repair-power behavior. Emit cumulative METRIC counters for timing_measured_vt_examined, timing_measured_vt_committed, timing_measured_vt_journal_rollbacks (including explicit zero), and timing_measured_vt_retained.",
    ),
    MechanismCard(
        "timing_recovery_mt1_deep_batch_progress",
        "timing_recovery_mt1_deep_batch_progress",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "mt1"),
        ("src/rsz/src/policy/SetupMt1Policy.cc", "src/rsz/src/policy/SetupMt1Policy.hh"),
        ("timing_mt1_deep_batches", "timing_mt1_deep_committed", "timing_mt1_deep_journal_rollbacks", "timing_mt1_deep_retained"),
        "For the controller-selected two-pass MT1 recipe only, refine SetupMt1Policy's existing batch journal boundary so that a measured legal batch is retained only when global TNS improves and it introduces no new electrical violation. Preserve worker partitioning, endpoint collection, move sequence, legalization, rollback, the controller recipe, and repair-power behavior. Emit cumulative METRIC counters for timing_mt1_deep_batches, timing_mt1_deep_committed, timing_mt1_deep_journal_rollbacks (including explicit zero), and timing_mt1_deep_retained.",
    ),
    # R28 validated the initial MT1 batch journal but still left 5.57 ns of
    # timing debt and re-touched many low-power cells.  These are distinct
    # source-level refinements, not a re-run of the integrated parent card.
    # They keep a real post-MT1 search space available after the first MT1
    # success, rather than allowing retrieval exhaustion to terminate the
    # campaign while the frozen timing goal remains unmet.
    MechanismCard(
        "timing_recovery_mt1_batch_frontier",
        "timing_recovery_mt1_batch_frontier",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "mt1", "batch_frontier"),
        ("src/rsz/src/policy/SetupMt1Policy.cc", "src/rsz/src/policy/SetupMt1Policy.hh"),
        ("timing_mt1_frontier_examined", "timing_mt1_frontier_retained"),
        "R28 validated the MT1 batch journal (15 measured batches, 14 retained) and improved post-route TNS to 17.57 ns, but timing repair re-touched many cells that the preceding repair_power reconstruction had changed. For only the controller-owned mt1_deep recipe, refine SetupMt1Policy's existing batch selection frontier before its already validated journal: among the existing legal worker batches, select the batch with the best measured global-TNS improvement per changed-instance footprint, then keep the existing journal retention, electrical checks, worker partitioning, endpoint collection, move sequence, legalization, rollback, pass limits, Tcl recipe, and repair_power reconstruction unchanged. Do not add an unbounded search, a numeric threshold sweep, a new move kind, or an external cell list. Emit METRIC|timing_mt1_frontier_examined|<positive> when an existing legal batch is measured at this frontier and METRIC|timing_mt1_frontier_retained|<positive> only when that batch survives the existing journal. Falsify unless official post-route normalized distance strictly improves from the MT1 parent while dynamic/leakage remain within their frozen targets, zero DRV and official 4/4 LEC hold.",
        refinement_signals=("timing_mt1_deep_batches", "timing_mt1_deep_retained"),
    ),
    MechanismCard(
        "timing_recovery_mt1_batch_bisection",
        "timing_recovery_mt1_batch_bisection",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "mt1", "journal", "batch_bisection"),
        ("src/rsz/src/policy/SetupMt1Policy.cc", "src/rsz/src/policy/SetupMt1Policy.hh"),
        ("timing_mt1_bisection_examined", "timing_mt1_bisection_retained", "timing_mt1_bisection_rolled_back"),
        "R28's validated MT1 policy journals an entire ranked worker batch, retaining it only when aggregate global TNS improves. The remaining timing gap may contain useful legal moves hidden inside a batch that is rolled back because another move offsets their gain. For only the controller-owned mt1_deep recipe, at that existing batch-journal rollback boundary, make one bounded deterministic recovery: after a real full-batch TNS rollback, test at most the two contiguous score-ranked halves using the existing journal, timing update, electrical checks, and strict global-TNS-improvement rule; retain only a half that independently passes the existing gate, otherwise restore it. Do not alter target generation, worker partitioning, score ordering, move kinds, library search, repair-power reconstruction, pass limits, Tcl, or add recursion/threshold sweeps. Emit timing_mt1_bisection_examined only for a real journaled half measured after its source batch was rolled back; emit timing_mt1_bisection_retained only after that half commits through the existing timing/electrical gate; emit timing_mt1_bisection_rolled_back only for an actually restored half. Falsify unless the official post-route result strictly lowers the matched mt1_deep distance and final TNS while dynamic <=350e9 pW, leakage <=35e6 pW, DRV remains zero, and official 4/4 LEC passes.",
        refinement_signals=("timing_mt1_deep_journal_rollbacks", "timing_mt1_deep_retained"),
    ),
    MechanismCard(
        "timing_recovery_tns_deep_endpoint_frontier",
        "timing_recovery_tns_deep_endpoint_frontier",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "tns_frontier"),
        ("src/rsz/src/policy/SetupTnsPolicy.cc", "src/rsz/src/policy/SetupTnsPolicy.hh"),
        ("timing_tns_frontier_examined", "timing_tns_frontier_retained"),
        "The current low-power MT1 parent has only TNS debt remaining. For the controller-owned tns_global recipe, refine SetupTnsPolicy's existing endpoint-improvement frontier so it measures the already enumerated legal endpoint repairs and retains the best current global-TNS improvement before advancing to another endpoint. Preserve endpoint enumeration, candidate/move ordering within an endpoint, legal/electrical checks, journals, repair limits, Tcl recipe, and repair_power reconstruction. Do not introduce named endpoints, a threshold sweep, or a new repair move. Emit METRIC|timing_tns_frontier_examined|<positive> for measured legal endpoint candidates and METRIC|timing_tns_frontier_retained|<positive> only after the existing journal retains one. Falsify unless complete official post-route distance strictly improves from the matched no-diff tns_global baseline with all integrity gates intact.",
        refinement_signals=("timing_mt1_deep_batches",),
    ),
    MechanismCard(
        "timing_recovery_reroute_deep_delay_frontier",
        "timing_recovery_reroute_deep_delay_frontier",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "reroute", "post_route"),
        ("src/rsz/src/policy/SetupReroutePolicy.cc", "src/rsz/src/policy/SetupReroutePolicy.hh"),
        ("timing_reroute_frontier_examined", "timing_reroute_frontier_retained"),
        "The low-power MT1 parent now has a post-route timing gap, while the earlier one-pass reroute probe was refuted. For only the controller-owned reroute_mid_power recipe, refine SetupReroutePolicy's existing legal critical-net candidate frontier: compare the policy's already estimated critical-net delay reduction before journal commit and prioritize the strongest measured reduction that does not worsen current global TNS. Preserve congestion/electrical checks, existing journals, fallback timing moves, global-route Tcl behavior, the bounded mid-area repair_power command, and all repair_power semantics. Do not alter route layers, invoke a new router command, or sweep route parameters. Emit METRIC|timing_reroute_frontier_examined|<positive> for measured legal candidates and METRIC|timing_reroute_frontier_retained|<positive> only for journal-retained reroutes. Falsify unless official post-route distance strictly improves from the matched no-diff baseline with zero DRV and official 4/4 LEC.",
        refinement_signals=("timing_mt1_deep_batches",),
    ),
    MechanismCard(
        "timing_recovery_crit_vt_deep_frontier",
        "timing_recovery_crit_vt_deep_frontier",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "timing_schedule_deep", "crit_vt"),
        ("src/rsz/src/policy/SetupCritVtSwapPolicy.cc", "src/rsz/src/policy/SetupCritVtSwapPolicy.hh"),
        ("timing_crit_vt_frontier_examined", "timing_crit_vt_frontier_retained"),
        "The validated MT1 parent already reserves power headroom for timing but still misses TNS. In SetupCritVtSwapPolicy's existing critical-VT candidate/journal boundary, add one bounded measured frontier that selects from the policy's existing legal VT alternatives by the best current global-TNS improvement, then applies the existing journal and electrical guards unchanged. Preserve the controller-owned deep timing recipe, candidate eligibility, cell-equivalence checks, timing updates, legalization, all repair_power behavior, and Tcl. Do not widen the library search, use a target-specific threshold, or introduce a global power objective. Emit METRIC|timing_crit_vt_frontier_examined|<positive> after an existing legal VT alternative is measured and METRIC|timing_crit_vt_frontier_retained|<positive> only after journal retention. Falsify unless official post-route normalized distance strictly improves from its matching baseline while dynamic/leakage stay within target and all integrity checks pass.",
        refinement_signals=("timing_mt1_deep_batches",),
    ),
    MechanismCard(
        "timing_recovery_mt1_phase_best_checkpoint",
        "timing_recovery_mt1_phase_best_checkpoint",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "mt1", "journal", "checkpoint"),
        ("src/rsz/src/policy/SetupMt1Policy.cc", "src/rsz/src/policy/SetupMt1Policy.hh"),
        ("timing_mt1_checkpoint_examined", "timing_mt1_checkpoint_retained", "timing_mt1_checkpoint_restored"),
        "R28 validates MT1's journaled timing recovery, while R29 conclusively refutes only the separate per-changed-instance-footprint selection frontier. For only the controller-owned mt1_deep recipe, add a phase-level best-state checkpoint at SetupMt1Policy's existing completed-worker-batch journal boundary: measure each already legal completed batch by the current global TNS, retain a new phase-best checkpoint only on strict global-TNS improvement, and restore the prior phase-best state when a later completed batch is worse. Preserve worker partitioning, candidate generation, move ordering, the validated batch journal, electrical checks, repair limits, recipe Tcl, and repair_power reconstruction. Do not reweight by footprint, add a new move, change worker count, sweep a threshold, or use an external cell list. Emit timing_mt1_checkpoint_examined for a completed measured batch, timing_mt1_checkpoint_retained only when its journal becomes the retained phase-best checkpoint, and timing_mt1_checkpoint_restored only for a real restoration of a later inferior batch. Falsify unless official post-route distance strictly improves both the exact mt1_deep no-diff baseline and fixed parent with lower final TNS, both power targets met, zero DRV, and official 4/4 LEC.",
        refinement_signals=("timing_mt1_deep_batches", "timing_mt1_deep_retained"),
    ),
    # R28/R44 establish that the MT1 batch path is both reachable and capable
    # of a real post-route gain.  The exhausted MT1 experiments changed batch
    # ordering or post-batch retention.  This is a different, earlier source
    # boundary: candidates are estimated from a shared pre-commit snapshot,
    # then committed serially after prior commits have invalidated that
    # snapshot.  Re-estimating only the existing candidates at the actual
    # commit boundary makes that stale-decision hazard observable without
    # expanding the move space or relaxing any acceptance guard.
    MechanismCard(
        "timing_recovery_mt1_commit_freshness",
        "timing_recovery_mt1_commit_freshness",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "mt1", "stale_estimate", "journal"),
        ("src/rsz/src/policy/SetupMt1Policy.cc", "src/rsz/src/policy/SetupMt1Policy.hh"),
        ("timing_mt1_fresh_reestimated", "timing_mt1_fresh_choice_changed", "timing_mt1_fresh_retained"),
        "For only the controller-owned mt1_deep recipe, correct the stale-estimate boundary in SetupMt1Policy::commitAndUpdateTiming. The policy currently generates and estimates all legal candidates from one pre-commit STA snapshot, ranks the per-target winners, then commits them serially; a preceding committed ECO can invalidate a later winner's timing estimate. Immediately before each existing journaled commit, re-estimate only that target's already generated, legal candidate vector against the current timing state and select the best current candidate using the policy's existing score and deterministic tie behavior. Do not generate extra candidates, add move types, alter endpoint collection, change the global batch ordering, worker count, move limit, Tcl recipe, repair_power rebuild, journals, electrical checks, or final TNS acceptance predicate. Emit METRIC|timing_mt1_fresh_reestimated|<positive> only after an existing candidate vector is genuinely re-estimated at this serial commit boundary; emit METRIC|timing_mt1_fresh_choice_changed|<positive> only when that current estimate replaces the stale selected winner; emit METRIC|timing_mt1_fresh_retained|<positive> only after the existing batch journal commits a batch containing a freshly selected move. Falsify unless the complete power_then_timing official post-route run strictly improves both the exact mt1_deep no-diff baseline and the fixed parent, lowers final TNS, keeps dynamic <=350e9 pW and leakage <=35e6 pW, has zero DRV, and passes official 4/4 LEC.",
        refinement_signals=("timing_mt1_deep_batches", "timing_mt1_deep_retained"),
        activation_signals=("timing_mt1_fresh_reestimated", "timing_mt1_fresh_retained"),
    ),
    # The validated MT1 path intentionally owns its compact move set instead
    # of inheriting the larger LEGACY sequence.  Its current generator switch
    # explicitly drops kBuffer, so the early worst-path batch cannot compare
    # a legal buffer insertion against its VT/size-up alternatives.  This is
    # a move-space boundary, not a ranking, stale-estimate, or checkpoint
    # revision already tested above.
    MechanismCard(
        "timing_recovery_mt1_buffer_generator_admission",
        "timing_recovery_mt1_buffer_generator_admission",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "mt1", "buffer", "move_space"),
        ("src/rsz/src/policy/SetupMt1Policy.cc", "src/rsz/src/policy/SetupMt1Policy.hh"),
        ("timing_mt1_buffer_examined", "timing_mt1_buffer_retained"),
        "For only the controller-owned mt1_deep recipe, make the existing BufferGenerator reachable at SetupMt1Policy's already executed move-generator construction boundary. MT1 currently builds only VT-swap and size-up generators and explicitly drops MoveType::kBuffer, even though the inherited OptimizationPolicy supplies the legal buffer generator and the same journaled candidate/commit path. Add the existing buffer move type to the MT1 sequence only when buffering is not skipped, instantiate BufferGenerator through the existing GeneratorContext, and let it compete under the unchanged per-target candidate estimation, global order, move cap, batch journal, timing update, electrical checks, and final TNS acceptance. Do not change target collection, candidate scoring, generator implementation, buffer library search, repair_power reconstruction, Tcl recipe, worker count, or any guard. Emit METRIC|timing_mt1_buffer_examined|<positive> only when an eligible generated buffer candidate reaches MT1's existing estimate/commit decision; emit METRIC|timing_mt1_buffer_retained|<positive> only after the existing batch journal commits a buffer move. Falsify unless complete official power_then_timing post-route QoR strictly improves both the exact mt1_deep baseline and current parent with lower TNS, dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC.",
        refinement_signals=("timing_mt1_deep_batches", "timing_mt1_deep_retained"),
        activation_signals=("timing_mt1_buffer_examined",),
    ),
    # MEASURED_CRIT_PATH is a separate, already-built timing policy: unlike
    # MT1 it measures legal candidates on actual violating paths and supports
    # a broader but bounded generator set.  It has never been admitted to the
    # timing-recovery controller, so its source-local global-TNS decision is a
    # fresh experiment rather than another MT1 or RMP retry.
    MechanismCard(
        "timing_recovery_measured_critical_path_tns_admission",
        "timing_recovery_measured_critical_path_tns_admission",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "measured_crit", "critical_path", "global_tns"),
        ("src/rsz/src/policy/MeasuredCriticalPathPolicy.cc", "src/rsz/src/policy/MeasuredCriticalPathPolicy.hh"),
        ("measured_crit_tns_examined", "measured_crit_tns_retained"),
        "For only the controller-owned measured_critical_path_deep recipe, use MeasuredCriticalPathPolicy's existing real path measurement and journaled commit boundary to make a bounded global-TNS admission decision. The current policy compares candidate score using mixed WNS/endpoint terms and can accept a local WNS-only improvement while the campaign's only remaining residual is aggregate TNS. After the policy's existing candidate timing measurement (do not introduce a new STA query), prefer or retain a legal candidate only when its already measured aggregate/global TNS improves from the current path-round baseline; retain the policy's existing finite WNS damage, endpoint, power/cap, route-shadow, candidate-generation, move-type, target-count, journal, restore, electrical, and max-move guards. Do not edit Tcl, set an environment threshold, change the controller recipe, widen endpoints, or alter repair_power reconstruction. Emit METRIC|measured_crit_tns_examined|<positive> only for an existing legal candidate with a real global-TNS comparison, and METRIC|measured_crit_tns_retained|<positive> only after its existing journaled commit survives. Falsify unless complete official power_then_timing post-route QoR strictly improves both the matching measured_critical_path_deep no-diff baseline and the current parent, lowers TNS, keeps dynamic <=350e9 pW and leakage <=35e6 pW, has zero DRV, and passes official 4/4 LEC.",
        activation_signals=("measured_crit_tns_examined",),
    ),
    # R47/R48 exposed a shared engineering boundary, not a refutation of all
    # buffer moves: a candidate can survive policy admission and then enter
    # Rebuffer with a top-level or otherwise non-materializable driver.  The
    # resulting SIGSEGV prevents the timing policy from completing and makes
    # its QoR measurement meaningless.  Guard it at generation time so every
    # policy using BufferGenerator has the same safe, auditable move space.
    MechanismCard(
        "timing_recovery_measured_critical_buffer_target_admission",
        "timing_recovery_buffer_target_admission",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "measured_critical", "buffer", "correctness", "move_space"),
        ("src/rsz/src/move/BufferGenerator.cc", "src/rsz/src/move/BufferCandidate.cc"),
        ("timing_buffer_target_rejected", "timing_buffer_target_examined"),
        "For only the controller-owned measured_critical_path_deep timing recipe, repair the shared BufferGenerator/BufferCandidate admission boundary exposed by R47/R48. Before a BufferCandidate becomes committable, reject a target whose driver pin is null, is a top-level port, lacks a network/database net required by Rebuffer, or fails the existing Resizer bufferability test; preserve every already legal internal target and all candidate estimation, generator ordering, library search, journal, global-TNS, electrical, rollback, and final acceptance guards. The change must make the policy safe to finish rather than disable buffering or replace it with a threshold/score sweep. Emit METRIC|timing_buffer_target_rejected|<positive> only for a candidate rejected by this new structural admission boundary, and METRIC|timing_buffer_target_examined|<positive> only when a structurally safe buffer candidate reaches the existing estimate/commit path. Do not edit Tcl, controller recipe, repair_power reconstruction, target collection, or any previously refuted MT1/MeasuredCritical ranking rule. Falsify unless the complete post-route result has zero DRV, official 4/4 LEC, dynamic <=350e9 pW, leakage <=35e6 pW, and a strict normalized-distance improvement with lower TNS over both its exact-recipe baseline and the parent.",
        activation_signals=("timing_buffer_target_examined",),
    ),
    MechanismCard(
        "timing_recovery_rmp_delay_sta_bracket",
        "timing_recovery_rmp_delay_sta_bracket",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "logic_restructure", "rmp", "post_route"),
        ("src/rmp/src/Restructure.cpp",),
        # Reaching a real STA trial establishes that this source mechanism
        # executed.  Retention is an outcome counter (and may correctly be
        # zero when every guarded trial is rejected), so it is recorded in
        # the log but must not turn a valid negative experiment into a
        # spurious "missing telemetry" repair.
        ("rmp_timing_examined",),
        "The R28 low-power parent has only post-route TNS debt. Its repair_timing policies have been explored, while the built RMP delay engine already has reversible cloud snapshots, legal-combinational filtering, real STA trial of existing ABC modes, and a best-mode selection boundary. For only the rmp_delay_timing recipe, refine a source-side cloud-quality, placement, or real STA-selection mechanism at Restructure's existing boundaries: report candidates only when a legal cloud reaches real STA trial, retain only an existing ABC mode with strict global TNS improvement while preserving WNS and the existing local area/leakage/HPWL/sequential-pin guards, and restore every inferior trial. The controller already sets RMP_STA_SELECT_BEST_MODE=1, RMP_MAX_CLOUDS=4, RMP_ENDPOINT_PATH_COUNT=4, and RMP_UNIQUE_ENDPOINTS=1; do not reimplement, remove, bypass, or broaden those controller-owned gates. Preserve ABC modes, snapshots, accepted-cloud/try budgets, and all repair_power behavior. Do not add a target-net list, relax any guard, add an unbounded loop, or change Tcl outside the controller-owned bounded recipe. Emit METRIC|rmp_timing_examined|<positive> only for actual STA-trial modes and METRIC|rmp_timing_retained|<positive> only after a selected mode survives existing acceptance. Falsify unless official post-route TNS improves below the R28 parent while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        required_flow_commands=("restructure",),
    ),
    MechanismCard(
        "timing_recovery_rmp_path_cone_v4",
        "timing_recovery_rmp_path_cone_v4",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "logic_restructure", "rmp", "post_route"),
        ("src/rmp/src/Restructure.cpp",),
        ("rmp_path_cone_examined", "rmp_timing_examined"),
        "R36 v3 proved that four distinct endpoint clouds do reach real STA trial, but every selected cloud contained only one to four instances and every ABC mode worsened TNS/WNS. For only the controller-owned rmp_path_cone_timing recipe, make one bounded source-side path-cone quality refinement at Restructure's existing endpoint/cloud construction boundary: distinguish an eligible unioned endpoint cone from a trivial path fragment, preserve the complete path core, and use the existing legal-combinational checks before the existing snapshot/ABC/real-STA selection path. The v4 controller already sets RMP_UNION_ENDPOINT_PATHS=1, RMP_ENDPOINT_PATH_COUNT=4, RMP_MAX_CLOUDS=4, RMP_MAX_TRIED_CLOUDS=4, RMP_UNIQUE_ENDPOINTS=1, and one bounded upstream fanin expansion (level 1, at most 16 instances). Do not change Tcl, ABC modes, accepted-cloud/try budgets, Liberty construction, or any existing STA/local-cost/HPWL/sequential-pin guard. Do not hard-code endpoints, lower a guard, add an unbounded traversal, or force acceptance. Emit METRIC|rmp_path_cone_examined|<positive> only when a nontrivial legal unioned cone actually reaches the existing RMP candidate path; retain METRIC|rmp_timing_examined| only for real STA trials. Falsify unless official post-route normalized distance strictly improves from its exact v4 no-diff baseline while TNS improves below R28, dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC hold.",
        required_flow_commands=("restructure",),
        # v3 emitted rmp_timing_examined. This is an explicitly new
        # controller/source boundary, so use that evidence to prioritize the
        # cone-quality refinement rather than repeat its small-cloud trial.
        refinement_signals=("rmp_timing_examined",),
    ),
    MechanismCard(
        "timing_recovery_rmp_path_cone_halo_v5",
        "timing_recovery_rmp_path_cone_halo_v5",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "logic_restructure", "rmp", "post_route"),
        ("src/rmp/src/Restructure.cpp",),
        ("rmp_path_cone_admission_decided",),
        "R37 v4 proved the exact failure boundary: unioning four paths still leaves each path core at one to four instances, and rejecting those cores leaves RMP's generic findFanins fallback to synthesize a 282-instance blob whose every guarded STA mode regresses. For only the controller-owned rmp_path_cone_halo_timing recipe, correct the source-side admission order at Restructure's existing path-cone/fallback boundary. First form the existing bounded unioned cone, run only the controller-owned bounded one-level fanin halo, restore the complete path core after any existing pruning, then decide whether the resulting live cone is nontrivial; do not reject solely from the pre-halo core size. When the bounded path-cone set is empty, block the generic all-fanin blob fallback for this v5 recipe rather than silently changing the experiment. Emit METRIC|rmp_path_cone_admission_decided|<positive> exactly for a real nontrivial-cone admission or a real no-candidate fallback block; it must not be telemetry before the decision. Preserve the four-cloud/one-accepted bounds, all existing legal-combinational, snapshot/restore, ABC, real-STA, TNS/WNS, local-cost, HPWL, leakage, sequential-pin, Liberty, repair_power, and repair_timing guards. Do not hard-code endpoints, force a candidate, lower a guard, add an unbounded traversal, or alter Tcl. Falsify unless this decision is visible in the official log and any admitted cone follows the existing guarded trial path; QoR succeeds only on a strict post-route normalized-distance gain from the exact v5 baseline with dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC.",
        required_flow_commands=("restructure",),
        refinement_signals=("rmp_path_cone_examined",),
    ),
    MechanismCard(
        "timing_recovery_repair_power_stability_frontier",
        "timing_recovery_repair_power_stability_frontier",
        ("tns", "timing", "timing_recovery", "timing_power_rebuild", "repair_timing_direct", "power_reclaim", "cell_reversion"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_stability_examined", "repair_power_stability_retained"),
        "The fixed R28 rebuild reaches both power targets but creates the dominant timing debt: TNS grows from about 9.35 ns before repair_power to about 60.34 ns immediately after early_forced_reclaim, and later timing repair re-touches roughly half of the power-replaced cells. For the existing top-level repair_power early_forced_reclaim path used before the controller-owned mt1_deep recovery recipe, refine RepairPowerPolicy's already generated positive-score candidates at its existing ordering/journal boundary: first measure a candidate's existing target slack/timing robustness, then prefer the more timing-robust candidates before otherwise comparable power-score candidates. Retain the existing candidate generation, move classes, quotas, adaptive windows, per-move and window journals, phase/full timing budgets, cap/fanout checks, and repair_timing semantics. Do not change Tcl, proportions, move limits, thresholds, the target list, or force a power target. Emit METRIC|repair_power_stability_examined|<positive> only for a real generated candidate examined by this stability-aware ordering, and METRIC|repair_power_stability_retained|<positive> only for a journal-retained move selected through it. Falsify unless the full post-route run strictly lowers normalized distance from its exact recipe baseline while TNS improves and dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC hold.",
        required_flow_commands=("repair_power", "repair_timing"),
        refinement_signals=("repair_power_committed",),
    ),
    MechanismCard(
        "timing_recovery_repair_power_reversion_exclusion",
        "timing_recovery_repair_power_reversion_exclusion",
        ("tns", "timing", "timing_recovery", "timing_power_rebuild", "repair_timing_direct", "power_reclaim", "cell_reversion"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_reversion_excluded", "repair_power_persistent_retained"),
        "The fixed R28 low-power rebuild has material headroom below both power targets, but its early_forced_reclaim phase creates the dominant TNS debt and roughly half of its power-replaced cells are subsequently revisited by timing repair. The prior stability-frontier experiment only changed candidate ordering and was refuted; do not repeat it. At the top-level repair_power early_forced_reclaim policy's existing candidate-admission boundary, use the already available pre-reclaim timing-path/cone information to exclude a power candidate before journaling when its target belongs to the current critical timing cone that the immediately following repair_timing must repair. This is a persistence admission decision, not a score reorder or a new timing cap: preserve candidate generation, positive-power eligibility, move classes, quotas, existing per-move/window journals, timing/electrical/fanout guards, controller command, and all repair_timing behavior. Emit METRIC|repair_power_reversion_excluded|<positive> only for an otherwise eligible candidate genuinely excluded through this critical-cone persistence decision; emit METRIC|repair_power_persistent_retained|<positive> only for a final journal-retained non-excluded power move. Do not change Tcl, proportions, move limits, endpoint lists, thresholds, or force a target. Falsify unless full post-route QoR strictly improves over the exact recipe baseline and R28 parent, with lower final TNS, dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC.",
        required_flow_commands=("repair_power", "repair_timing"),
        refinement_signals=("repair_power_committed",),
    ),
    MechanismCard(
        "timing_recovery_repair_power_reversion_mt1_recovery",
        "timing_recovery_repair_power_reversion_mt1_recovery",
        ("tns", "timing", "timing_recovery", "timing_power_rebuild", "repair_timing_direct", "power_reclaim", "cell_reversion", "mt1", "controller_revision"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_reversion_excluded", "repair_power_persistent_retained"),
        "R43 proved that the early repair_power critical-path persistence admission is genuinely active (both exclusion and retained-move signals fired), but evaluated it only with the controller-owned legacy_deep timing recovery, whose post-power recovery was much weaker than the validated MT1 lineage. Treat mt1_deep as a named controller revision and a fresh exact-recipe baseline: reproduce the already proven source-local persistence admission exactly, without changing its membership rule, move ordering, quotas, timing guards, Tcl, or controller command, then execute the bounded mt1_deep recovery schedule after the same low-power rebuild. This experiment changes only the verified recovery schedule, not the source mechanism. Emit the same exclusion and retained signals only for the existing real actions. Falsify unless the complete post-route result strictly improves both the matched mt1_deep no-diff baseline and R28 parent, with lower final TNS, dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC. Do not reinterpret the legacy_deep refutation as a source-level suppression under this named controller revision.",
        required_flow_commands=("repair_power", "repair_timing"),
        refinement_signals=("repair_power_reversion_excluded", "repair_power_persistent_retained"),
    ),
    # RMP already evaluates bounded ABC modes with real STA and restores every
    # rejected snapshot.  This is a distinct final-admission boundary: local
    # wire proxies may veto a mode that has passed global TNS/WNS and all
    # sequential/area/leakage guards, despite TNS being the sole remaining
    # frozen-contract residual.
    MechanismCard(
        "timing_recovery_rmp_global_tns_wire_guard",
        "timing_recovery_rmp_global_tns_wire_guard",
        ("tns", "timing", "timing_recovery", "repair_timing_direct", "rmp", "restructure", "global_tns", "wire_guard"),
        ("src/rmp/src/Restructure.cpp",),
        ("rmp_tns_wire_examined", "rmp_tns_wire_retained"),
        "For only the controller-owned rmp_delay_timing recipe, refine Restructure.cpp's existing RMP_STA_SELECT_BEST_MODE trial-rejection boundary. A bounded ABC mode is already snapshot-restored, parasitics-estimated, and checked by real global STA, sequential-pin preservation, local area/leakage cost, and local wire cost. Preserve the controller's one-cloud/try budgets, ABC modes, snapshot/restore, all sequential-pin and local area/leakage guards, and the exact global TNS/WNS contract guards. Change only the local-wire rejection handling: when a mode has already passed the global TNS/WNS guards with strict positive global TNS gain, record that it was evaluated at the wire boundary and permit it to compete for best mode despite a local HPWL/net-count proxy rejection only if no other existing hard rejection remains. Do not disable wire measurement, alter its thresholds, accept a mode without global STA improvement, change Tcl, broaden clouds, add a loop, or modify repair_power. Emit METRIC|rmp_tns_wire_examined|<positive> only for a real mode that passes global timing before this wire decision, and METRIC|rmp_tns_wire_retained|<positive> only after that existing selected mode survives the normal final snapshot/commit path. Falsify unless official post-route QoR strictly lowers the three-metric normalized distance and final TNS while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all pass.",
        activation_signals=("rmp_tns_wire_examined",),
        conclusive_nonactivation_patterns=("RMP_GUARD|sta_select_no_accept",),
        required_flow_commands=("restructure",),
    ),
    # R44's complete checkpoint trace identifies a specific power-to-timing
    # conflict that is not a generic early-reclaim cap experiment.  The
    # low-power rebuild retained 7,750 combined size-down + power-VT actions,
    # then the timing recipe performed 12,259 size-ups and revisited over half
    # of the power-replaced cells.  The compound move has no independent
    # quota, unlike pure power-VT and buffer-removal actions.
    MechanismCard(
        "timing_recovery_early_compound_vt_reserve",
        "timing_recovery_early_compound_vt_reserve",
        ("tns", "timing", "timing_recovery", "repair_power", "power_timing_tradeoff", "vt", "sizing"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_compound_vt_examined", "repair_power_compound_vt_retained"),
        "The complete R44 low-power rebuild reached 12,571 early_forced_reclaim commits: 7,750 were the mixed kSizeDownAndPowerVtSwap kind, while later repair_timing resized 12,259 instances and revisited about 52% of power-replaced cells. This is a distinct causal boundary from the already tested generic cap, ranking, and critical-cone rules: RepairPowerPolicy tracks quotas for pure power-VT and unbuffer moves but no independently auditable reserve for the high timing-cost compound size-down+VT class. At the existing early_forced_reclaim candidate admission/commit boundary, add one deterministic compound-move quota equal to one half of the already configured early max-move budget (6,250 under the observed 12,500 cap), not a Tcl knob or a threshold sweep. It must limit only kSizeDownAndPowerVtSwap, so it actually constrains the observed 7,750 compound actions while preserving pure size-down, pure power-VT, buffer-removal, target collection, candidate scoring/order, critical-cone exclusion, adaptive windows, per-candidate/window journals, max-cap and electrical guards, phase timing budget, and repair_timing policies unchanged. The quota must apply consistently to both single and window commits, and should emit METRIC|repair_power_compound_vt_examined|<positive> only after an otherwise legal compound candidate reaches this quota decision and METRIC|repair_power_compound_vt_retained|<positive> only after a compound move survives the existing journal. Do not edit Tcl, change the 80% command proportion, broaden move types, alter the global early move cap, or weaken any guard. Falsify unless complete post-route QoR strictly improves the normalized three-metric distance and final TNS over the matched mt1_deep baseline while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        activation_signals=("repair_power_compound_vt_examined",),
        required_flow_commands=("repair_power", "repair_timing"),
    ),
    # R51 validated the compound quota but also showed its limitation: only
    # 116 of 6,366 legal compound candidates were rejected after the 6,250
    # reserve filled, while 45.7% of early power replacements were revisited
    # by timing repair.  The existing direct critical-path guard is active,
    # but it protects only an exact current path member.  A one-hop closure is
    # a distinct source mechanism, rather than another quota value.
    MechanismCard(
        "timing_recovery_compound_vt_critical_halo",
        "timing_recovery_compound_vt_critical_halo",
        ("tns", "timing", "timing_recovery", "repair_power", "power_timing_tradeoff", "critical_cone", "vt", "sizing"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_compound_halo_examined", "repair_power_compound_halo_excluded"),
        "R51 validated the early compound reserve, but its telemetry shows only 116 quota rejections among 6,366 otherwise legal compound candidates, while later repair_timing still revisited 45.7% of early power replacements. RepairPowerPolicy already computes and enforces a direct current critical-path membership guard before its journaled commits; the untested causal boundary is its zero-radius membership. At that existing early_forced_reclaim cone-capture/admission boundary, construct exactly one combinational one-hop fanout closure of the already selected direct critical-path driver instances, and use it only to exclude kSizeDownAndPowerVtSwap candidates whose target is in that halo. Preserve the existing direct-cone exclusion for every move kind, target collection/order, the validated 6,250 compound quota, pure size-down/pure power-VT/buffer moves, adaptive windows, max-cap/electrical/timing guards, journals, rollback, phase budget, repair_timing policies, Tcl, and command proportion. Do not introduce a radius knob, a slack threshold, a second path search, or a generic fanout filter. Emit METRIC|repair_power_compound_halo_examined|<positive> only when an otherwise legal compound candidate matches this new closure and METRIC|repair_power_compound_halo_excluded|<positive> only when that candidate is excluded before journal mutation. Falsify unless a fresh matched mt1_deep no-diff baseline and complete post-route result strictly improve normalized three-metric distance and final TNS versus R51 while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        activation_signals=("repair_power_compound_halo_examined",),
        required_flow_commands=("repair_power", "repair_timing"),
    ),
    # R52 validates the downstream one-hop compound guard: it examined 7,208
    # otherwise admissible compound candidates and excluded 577, reducing
    # post-route TNS by 0.68 ns while retaining both power targets.  Its
    # direction is deliberately limited to fanout.  The remaining timing debt
    # and 46.3% power-to-timing overlap identify the untested *upstream*
    # adjacency of the same direct path drivers as the next bounded causal
    # boundary; this is not a radius increase or a retest of the fanout halo.
    MechanismCard(
        "timing_recovery_compound_vt_critical_fanin_halo",
        "timing_recovery_compound_vt_critical_fanin_halo",
        ("tns", "timing", "timing_recovery", "repair_power", "power_timing_tradeoff", "critical_cone", "fanin", "vt", "sizing"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_compound_fanin_halo_examined", "repair_power_compound_fanin_halo_excluded"),
        "R52 proved that excluding 577 otherwise legal compound size-down+power-VT moves in the one-hop *fanout* of direct critical-path drivers lowers final TNS, but repair_power remains the diagnosed timing-debt stage and later repair_timing still re-touches 46.3% of power replacements. The complementary, untested structural boundary is the one-hop combinational *fanin* of those same already selected direct critical-path driver instances. At RepairPowerPolicy's existing early_forced_reclaim cone-capture/admission boundary, construct exactly that single upstream combinational predecessor closure and exclude only kSizeDownAndPowerVtSwap candidates whose target is in it. Keep the validated direct-cone exclusion and existing one-hop fanout halo intact; this new closure must not be a radius knob, a second path search, a generic transitive traversal, or a filter for any other move kind. Preserve candidate generation/order, the validated 6,250 compound quota, pure size-down/pure power-VT/buffer moves, adaptive windows, max-cap/electrical/timing guards, journals, rollback, phase budgets, repair_timing policies, Tcl, and command proportion. Emit METRIC|repair_power_compound_fanin_halo_examined|<positive> only when an otherwise legal compound candidate reaches this new fanin admission decision, and METRIC|repair_power_compound_fanin_halo_excluded|<positive> only when that candidate is excluded before journal mutation. Falsify unless a fresh matched mt1_deep no-diff baseline and full official post-route result strictly improve normalized three-metric distance and final TNS versus R52 while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        activation_signals=("repair_power_compound_fanin_halo_examined",),
        required_flow_commands=("repair_power", "repair_timing"),
    ),
    # R53 retained the compound quota and both one-hop protections, yet the
    # fixed early profile still commits 2,133 pure power-VT swaps.  These
    # swaps deliberately trade timing robustness for leakage and are not
    # governed by the compound-only halos.  Move-kind coverage is a distinct
    # admission boundary, not a re-run of either closure experiment.
    MechanismCard(
        "timing_recovery_pure_vt_critical_halo",
        "timing_recovery_pure_vt_critical_halo",
        ("tns", "timing", "timing_recovery", "repair_power", "power_timing_tradeoff", "critical_cone", "vt"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_pure_vt_halo_examined", "repair_power_pure_vt_halo_excluded"),
        "R53 validated the direct critical exclusion, the 6,250 compound reserve, and separate one-hop fanout/fanin halos for kSizeDownAndPowerVtSwap, but the same early_forced_reclaim execution still commits 2,133 pure kPowerVtSwap moves. These pure VT swaps are a separate move class that can consume local timing margin without any size-down, and neither validated halo applies to them. At the existing early candidate admission boundary, reuse the already constructed union of the validated one-hop fanout and one-hop fanin sets solely to exclude an otherwise legal kPowerVtSwap whose target is in either set. Do not alter the direct-cone exclusion, compound-only quota or halos, pure-VT candidate generation/order, max pure-VT quota, size-down/buffer moves, adaptive windows, electrical/timing guards, journals, rollback, phase budgets, repair_timing policies, Tcl, or command proportion. Emit METRIC|repair_power_pure_vt_halo_examined|<positive> only when an otherwise legal pure power-VT candidate reaches this new union admission decision, and METRIC|repair_power_pure_vt_halo_excluded|<positive> only when it is excluded before journal mutation. Falsify unless a fresh matched mt1_deep baseline and full official post-route run strictly improve normalized three-metric distance and final TNS versus R53 while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        activation_signals=("repair_power_pure_vt_halo_examined",),
        required_flow_commands=("repair_power", "repair_timing"),
    ),
    # R54 extended the validated compound fanin/fanout protection to pure VT
    # swaps and improved post-route TNS while retaining substantial headroom
    # to both power limits.  Pure size-down is the remaining early-reclaim
    # move kind that can reduce a critical driver's strength without either
    # the compound quota or the existing halo protections.  This is a bounded
    # move-kind coverage experiment, not a wider timing-cone traversal.
    MechanismCard(
        "timing_recovery_size_down_critical_halo",
        "timing_recovery_size_down_critical_halo",
        ("tns", "timing", "timing_recovery", "repair_power", "power_timing_tradeoff", "critical_cone", "sizing"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_size_down_halo_examined", "repair_power_size_down_halo_excluded"),
        "R54 validated the direct critical exclusion, the 6,250 compound reserve, the one-hop fanout/fanin protections for compound moves, and their union for pure kPowerVtSwap. The remaining early_forced_reclaim move kind that can reduce critical-driver strength is pure kSizeDown: it is not covered by the compound-only quota or either existing halo. At the existing early candidate admission boundary, reuse exactly the already constructed union of the validated one-hop fanout and one-hop fanin sets solely to exclude an otherwise legal kSizeDown candidate whose target is in either set. This intentionally permits a bounded increase in power within the frozen dynamic <=350e9 pW and leakage <=35e6 pW contracts in return for lower final TNS. Do not alter the direct-cone exclusion, compound quota or halos, pure-VT halo, candidate generation/order, move limits, adaptive windows, electrical/timing guards, journals, rollback, phase budgets, repair_timing policies, Tcl, or command proportion. Apply the guard consistently to both single and window commits. Emit METRIC|repair_power_size_down_halo_examined|<positive> only when an otherwise legal pure size-down candidate reaches this union admission decision, and METRIC|repair_power_size_down_halo_excluded|<positive> only when it is excluded before journal mutation. Falsify unless a fresh matched mt1_deep baseline and full official post-route run strictly improve normalized three-metric distance and final TNS versus R54 while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        activation_signals=("repair_power_size_down_halo_examined",),
        required_flow_commands=("repair_power", "repair_timing"),
    ),
    # R55 falsified the remaining pure-size-down coverage: protecting its
    # 48 halo hits was insufficient and slightly worsened final TNS.  Early
    # buffer removal is a separate structural move (589 retained actions in
    # R54/R55) that removes delay-isolating cells rather than swapping a
    # replacement; it therefore needs its own admission boundary.
    MechanismCard(
        "timing_recovery_buffer_removal_critical_halo",
        "timing_recovery_buffer_removal_critical_halo",
        ("tns", "timing", "timing_recovery", "repair_power", "power_timing_tradeoff", "critical_cone", "buffer_removal"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_buffer_halo_examined", "repair_power_buffer_halo_excluded"),
        "R55 showed that guarding pure kSizeDown candidates in the validated one-hop fanout/fanin union changes only 48 admissions and does not improve post-route TNS. In the same early_forced_reclaim execution, kRemoveBuffer is a distinct structural move with roughly 589 retained actions: it removes an inserted delay-isolating cell and is not governed by the compound, pure-VT, or size-down halo rules. At RepairPowerPolicy's existing single-candidate buffer-removal admission boundary, reuse exactly the already constructed union of the validated one-hop fanout and one-hop fanin sets solely to reject an otherwise legal kRemoveBuffer target in that union before journal mutation. Do not alter the direct-cone exclusion, buffer quota/fanout/prechecks, candidate generation/order, compound quota/halos, pure-VT or size-down rules, adaptive windows, electrical/timing guards, journals, rollback, phase budgets, repair_timing policies, Tcl, or command proportion. This intentionally permits a bounded power increase within dynamic <=350e9 pW and leakage <=35e6 pW in return for lower TNS. Emit METRIC|repair_power_buffer_halo_examined|<positive> only for a real otherwise legal buffer-removal candidate reaching this decision and METRIC|repair_power_buffer_halo_excluded|<positive> only when it is rejected before a journal mutation. Falsify unless a fresh matched mt1_deep baseline and full official post-route result strictly improve normalized three-metric distance and final TNS versus R54 while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        activation_signals=("repair_power_buffer_halo_examined",),
        required_flow_commands=("repair_power", "repair_timing"),
    ),
    # The R56 buffer-halo experiment was strongly refuted: even a small
    # buffer-removal exclusion destabilised the final routing result.  The
    # measured dominant timing debt instead remains the high-volume compound
    # size-down+power-VT class.  Its validated 6,250 reserve can be tightened
    # by one fixed, evidence-derived amount while staying within the parent’s
    # 14B dynamic and 6.4M leakage margins.
    MechanismCard(
        "timing_recovery_compound_vt_timing_reserve",
        "timing_recovery_compound_vt_timing_reserve",
        ("tns", "timing", "timing_recovery", "repair_power", "power_timing_tradeoff", "compound_vt", "timing_budget"),
        ("src/rsz/src/policy/RepairPowerPolicy.cc", "src/rsz/src/policy/RepairPowerPolicy.hh"),
        ("repair_power_compound_timing_reserve_examined", "repair_power_compound_timing_reserve_excluded"),
        "R56 refuted another local-halo filter, while the diagnosed timing debt still originates in early_forced_reclaim and its 6,250 kSizeDownAndPowerVtSwap commits. The current R54 parent has dynamic headroom of about 14B pW and leakage headroom of 6.4M pW, so make one fixed source-side timing-reserve revision at RepairPowerPolicy's existing compound quota boundary: retain at most 5,750 compound moves (46% of the existing 12,500 early max-move budget) in early_forced_reclaim, rather than the currently retained half-budget reserve. This is a single contraction selected from measured headroom, not a Tcl knob, sweep, ranking change, or altered target list. It must apply consistently to single and window commits while preserving all existing direct-cone and fanin/fanout/pure-VT protections, non-compound moves, adaptive windows, per-candidate/window journals, electrical and phase guards, repair_timing schedule, Tcl, and command proportion. Emit METRIC|repair_power_compound_timing_reserve_examined|<positive> only for an otherwise legal compound candidate reaching this revised quota decision and METRIC|repair_power_compound_timing_reserve_excluded|<positive> only when the reserve rejects it before journal mutation. Falsify unless a fresh matched mt1_deep baseline and full official post-route result strictly improve normalized three-metric distance and final TNS versus R54 while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        activation_signals=("repair_power_compound_timing_reserve_examined",),
        required_flow_commands=("repair_power", "repair_timing"),
    ),
    MechanismCard(
        "timing_recovery_rmp_post_timing_halo",
        "timing_recovery_rmp_post_timing_halo",
        ("tns", "timing", "timing_recovery", "rmp", "restructure", "path_cone", "post_route"),
        ("src/rmp/src/Restructure.cpp",),
        ("rmp_post_timing_halo_examined",),
        "Earlier RMP experiments ran before timing recovery: their four-path endpoint cores had only one to four legal instances, and the generic 282-instance fanin fallback was either rejected by real STA or blocked. Use the new controller-owned rmp_post_timing_halo recipe, which first executes the bounded MT1/TNS timing recovery, then invokes RMP after that repair with a two-level upstream combinational halo capped at 24 added instances and no generic fanin fallback, and finally runs the same timing recovery tail. At Restructure.cpp's existing endpoint-cloud admission boundary, make one source-side post-timing decision: for this explicitly bounded post-timing halo recipe, emit METRIC|rmp_post_timing_halo_examined|<positive> only after a legal expanded endpoint cloud survives the existing complete path-core restoration, sequential/block checks, and path-cone-only admission and is about to reach the normal snapshot/ABC/real-STA trial path. Do not resurrect the generic all-fanin blob, alter cloud/try/accept budgets, hard-code endpoints, relax existing global TNS/WNS, local area/leakage, wire, sequential-pin, snapshot/restore, or ABC guards, or edit Tcl. Falsify unless a fresh exact post-timing-halo no-diff baseline and full official post-route result strictly improve normalized distance and final TNS versus R54 while dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and official 4/4 LEC all hold.",
        activation_signals=("rmp_post_timing_halo_examined",),
        required_flow_commands=("restructure", "repair_timing"),
    ),
    MechanismCard(
        "timing_recovery_rmp_post_timing_wire_trial",
        "timing_recovery_rmp_post_timing_wire_trial",
        ("tns", "timing", "timing_recovery", "rmp", "restructure", "post_route", "wire_guard"),
        ("src/rmp/src/Restructure.cpp",),
        ("rmp_post_timing_wire_examined",),
        "R58 established a useful controller fact: the exact post-timing RMP halo baseline lowers normalized distance to 0.138020 from the R54 parent’s 0.142145, but its source-side cloud-admission telemetry adds no further QoR gain. Continue under that exact rmp_post_timing_halo controller and alter only Restructure.cpp's existing local-wire trial-rejection boundary. For a bounded post-timing cloud that has already passed the existing real global TNS/WNS improvement checks, sequential-pin integrity, snapshot/restore, and local area/leakage checks, record METRIC|rmp_post_timing_wire_examined|<positive> and allow it to compete for the existing best accepted mode despite a local HPWL/net-count proxy rejection only when no other hard rejection remains. Preserve all cloud/path budgets, ABC modes, timing guards, route/cap/electrical checks, and rollback; do not accept a mode without strict global TNS improvement, change Tcl, resurrect generic fanin, broaden a cloud, or modify repair_power. Falsify unless a fresh exact post-timing-halo baseline and official post-route result strictly improve normalized distance and final TNS over that baseline and R54, with dynamic <=350e9 pW, leakage <=35e6 pW, zero DRV, and 4/4 LEC.",
        activation_signals=("rmp_post_timing_wire_examined",),
        required_flow_commands=("restructure", "repair_timing"),
    ),
)

_METRIC_SIGNAL = re.compile(r"METRIC\|([A-Za-z0-9_]+)\|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


class DiverseRetriever:
    """Evidence-oriented retrieval with MMR and round-memory suppression.

    A card is selected because it explains the active residual and names a source
    hook/signal. There is deliberately no unconditional paper-card tail.
    """

    name = "diverse_retriever"

    # These are the optimization commands emitted by the Contest-2026 Tcl
    # builder.  ``repair_power`` is intentionally a peer of
    # ``repair_timing``: Stage 1 invokes it directly, rather than treating a
    # power policy reached from repair_timing as an equivalent experiment.
    # Source hooks below an uninvoked command are not experiments: assigning
    # them asks a Student to change code that cannot fire.
    active_flow_commands = frozenset({"repair_design", "repair_power", "repair_timing", "restructure", "global_route"})

    def __init__(self, cards: Iterable[MechanismCard] = DEFAULT_CARDS, *, max_repeat_rounds: int = 1) -> None:
        self.cards = tuple(cards)
        self.max_repeat_rounds = max_repeat_rounds

    def retrieve(
        self,
        *,
        parent: Parent,
        symptoms: Sequence[str],
        state_root: Path,
        count: int,
    ) -> list[MechanismCard]:
        ledger_path = state_root / "knowledge" / "retrieval_ledger.json"
        ledger = load_json(ledger_path, {"rounds": []}) or {"rounds": []}
        feedback = load_json(state_root / "knowledge" / "feedback.json", {"suppressed_card_ids": []}) or {}
        suppressed = set(feedback.get("suppressed_card_ids") or [])
        activation_retry = set(feedback.get("activation_retry_card_ids") or [])
        # A validated edit selected as the current parent is already present in
        # every Student workspace.  Reassigning its original card asks Codex to
        # "integrate" a patch that has no source delta, then needlessly spends
        # a build and full contest evaluation.  Keep this distinct from a
        # merely validated losing sibling: only the promoted parent (and
        # explicitly recorded prior promotions) are integrated.
        integrated = set(feedback.get("integrated_card_ids") or [])
        integrated.update(self._current_parent_card_ids(state_root=state_root, parent=parent))
        activated = self._completed_flow_signals(state_root)
        recent = [entry for row in list(ledger.get("rounds") or [])[-self.max_repeat_rounds:] for entry in row.get("card_ids", [])]
        used_families = [entry for row in list(ledger.get("rounds") or [])[-1:] for entry in row.get("families", [])]
        symptom_set = {item.lower() for item in symptoms}
        prefer_established_direct = False

        # Mechanism-family labels are not sufficient to make a Student batch
        # independent.  Several cards can name different policies while
        # editing the same dispatcher or policy implementation.  That gives
        # the Teacher four superficially different hypotheses but only one
        # causal experiment.  For a broad multi-metric search, reserve the
        # first pass for source-disjoint scopes.  A route-stage or explicit
        # timing-debt diagnosis is intentionally exempt: those diagnoses may
        # require a coordinated set of refinements at one proven boundary.
        # The fallback below still fills every Student slot if the source tree
        # does not offer enough disjoint reachable mechanisms.
        enforce_source_scope_diversity = not bool(
            symptom_set.intersection({"route", "post_route", "timing_recovery"})
        )

        def score(card: MechanismCard) -> tuple[int, int, int, str]:
            relevance = len(symptom_set.intersection(tag.lower() for tag in card.symptom_tags))
            # A card whose previous run never emitted its expected signal was
            # not an executed experiment.  Give its corrected activation one
            # immediate retry instead of burying it behind the generic recent
            # round penalty.
            repeat_penalty = 0 if card.card_id in activation_retry else (5 if card.card_id in recent else 0)
            family_penalty = 2 if card.mechanism_family in used_families else 0
            feedback_penalty = 4 if card.card_id in suppressed else 0
            # A completed flow can expose a useful causal trade-off: a
            # mechanism fired and moved one QoR dimension, but was rejected
            # by the final contract.  Its named bounded repair deserves the
            # next experiment before unrelated generic cards.  This uses only
            # round.json-backed signal evidence, never an incomplete run.
            refinement_bonus = 4 if card.refinement_signals and set(card.refinement_signals).issubset(activated) else 0
            schedule_probe_penalty = (
                10
                if prefer_established_direct
                and {"timing_schedule_probe", "timing_schedule_deep"}.intersection(
                    tag.lower() for tag in card.symptom_tags
                )
                else 0
            )
            return (
                relevance + refinement_bonus - repeat_penalty - family_penalty - feedback_penalty - schedule_probe_penalty,
                refinement_bonus,
                relevance,
                card.card_id,
            )

        flow_reachable = [
            card
            for card in self.cards
            if card.candidate_eligible
            and set(card.required_flow_commands).issubset(self.active_flow_commands)
        ]
        # Runtime is intentionally observer-only for the AES QoR contract.
        # A runtime-only mechanism can be useful only when runtime is an
        # explicit unresolved target; otherwise it consumes a Student while
        # offering no path to TNS/dynamic/leakage improvement.
        if "runtime" not in symptom_set:
            flow_reachable = [
                card for card in flow_reachable if card.mechanism_family != "runtime_guard"
            ]
        if "power_reclaim" in symptom_set:
            # Stage 1 must exercise the public repair_power command and its
            # dedicated implementation chain.  A similarly named policy
            # reached only from repair_timing is a different experiment.
            flow_reachable = [
                card
                for card in flow_reachable
                if card.power_command_eligible
            ]
        # Activation cards are useful once: after a completed, official round
        # has already emitted a card's complete signal set, assigning the same
        # card again would normally add counters to an active parent mechanism
        # rather than searching a new decision.  This uses only completed
        # round logs (not aborted/local workspaces), so it is stable evidence
        # rather than speculative retrieval state.
        flow_reachable = [
            card
            for card in flow_reachable
            # A trade-off refinement may deliberately reuse an already seen
            # telemetry name while changing the bounded acceptance rule.  Its
            # own execution is still new evidence, so do not mistake a signal
            # emitted by an earlier (possibly refuted) mechanism for proof
            # that this repair card has already been evaluated.
            if card.refinement_signals
            or not card.expected_signals
            or not set(card.activation_signals or card.expected_signals).issubset(activated)
        ]
        # Completed final-QoR refutation is a hard admission decision.  Older
        # code revived suppressed cards merely to fill a parallel batch; that
        # recreated exactly the same hook/threshold experiment and consumed a
        # full build and post-route flow without new causal evidence.
        eligible = [
            card
            for card in flow_reachable
            if card.card_id not in suppressed and card.card_id not in integrated
        ]
        # Once Stage 1 has produced a power-complete parent, source edits that
        # merely seek more reclaim are not the primary experiment.  Prefer
        # cards that alter an already executed repair_timing policy directly.
        # If all direct mechanisms have complete negative evidence, retain the
        # wider pool rather than fabricating a duplicate timing card.
        if "timing_recovery" in symptom_set:
            direct_timing = [
                card
                for card in eligible
                if {"repair_timing_direct", "timing_power_rebuild"}.intersection(
                    tag.lower() for tag in card.symptom_tags
                )
            ]
            # A source hook in repair_timing is the first-line search while
            # such hooks remain untested.  Repair-power debt refinements are
            # the fallback once those direct controller mechanisms have been
            # exhausted or refuted; otherwise a high-scoring power card would
            # prematurely displace a purpose-built timing-policy experiment.
            repair_timing_direct = [
                card
                for card in direct_timing
                if "repair_timing_direct" in {tag.lower() for tag in card.symptom_tags}
            ]
            if repair_timing_direct:
                direct_timing = repair_timing_direct
            if direct_timing:
                # Schedule probes are a deliberately fresh source family for
                # a timing-recovery campaign after all established direct
                # cards have complete negative evidence.  They must not
                # displace an untested direct mechanism in a new campaign.
                established = [
                    card for card in direct_timing
                    if not {"timing_schedule_probe", "timing_schedule_deep"}.intersection(
                        tag.lower() for tag in card.symptom_tags
                    )
                ]
                prefer_established_direct = bool(established)
                preferred = established or direct_timing
                eligible = preferred
        ordered = sorted(eligible, key=score, reverse=True)
        selected: list[MechanismCard] = []
        families: set[str] = set()
        used_hooks: set[str] = set()
        for card in ordered:
            if card.card_id in recent and any(item.card_id not in recent for item in ordered):
                continue
            if card.mechanism_family in families and len(selected) < count:
                continue
            if enforce_source_scope_diversity and used_hooks.intersection(card.source_hooks):
                continue
            selected.append(card)
            families.add(card.mechanism_family)
            used_hooks.update(card.source_hooks)
            if len(selected) == count:
                break
        if len(selected) < count:
            selected.extend(card for card in ordered if card not in selected) 
            selected = selected[:count]
        return selected

    @staticmethod
    def _current_parent_card_ids(*, state_root: Path, parent: Parent) -> set[str]:
        """Return the retrieval card that produced the promoted parent.

        This compatibility path also protects campaigns created before
        ``integrated_card_ids`` was introduced. A parent imported from a
        different campaign intentionally has no local card identity.
        """
        match = re.fullmatch(r"round_(\d+):([^:]+)", parent.parent_id)
        if not match:
            return set()
        round_index, student_id = match.groups()
        hypothesis = load_json(
            state_root / "rounds" / f"round_{int(round_index):03d}" / "students" / student_id / "artifacts" / "hypothesis.json",
            {},
        )
        if not isinstance(hypothesis, dict):
            return set()
        return {str(card_id) for card_id in list(hypothesis.get("retrieval_ids") or []) if str(card_id)}

    @staticmethod
    def _completed_flow_signals(state_root: Path) -> set[str]:
        rounds = state_root / "rounds"
        if not rounds.is_dir():
            return set()
        signals: set[str] = set()
        for round_root in rounds.glob("round_*"):
            if not (round_root / "round.json").is_file():
                continue
            for log in round_root.glob("students/*/artifacts/contest_output/evaluation.log"):
                try:
                    signals.update(_METRIC_SIGNAL.findall(log.read_text(encoding="utf-8", errors="ignore")))
                except OSError:
                    continue
        return signals

    def record(self, *, state_root: Path, round_index: int, cards: Sequence[MechanismCard], symptoms: Sequence[str]) -> None:
        path = state_root / "knowledge" / "retrieval_ledger.json"
        ledger = load_json(path, {"schema_version": "goalevolve.v2.retrieval-ledger.v1", "rounds": []})
        ledger.setdefault("rounds", []).append(
            {"round": round_index, "card_ids": [card.card_id for card in cards], "families": [card.mechanism_family for card in cards], "symptoms": list(symptoms)}
        )
        atomic_json(path, ledger)

    def paper_card_references(
        self,
        *,
        parent: Parent,
        symptoms: Sequence[str],
        state_root: Path,
        count: int = 8,
    ) -> list[dict[str, object]]:
        """Return retrieved literature/mechanism cards as advice, never slots.

        A card is useful context for a Teacher but must not become an implicit
        controller-authored hypothesis. Per-design use is tracked separately
        and cards cited more than five times are demoted before retrieval.
        """
        path = state_root / "knowledge" / "paper_card_usage.json"
        payload = load_json(path, {"schema_version": "goalevolve.v2.paper-card-usage.v1", "counts": {}}) or {}
        counts = {
            str(card_id): int(value)
            for card_id, value in dict(payload.get("counts") or {}).items()
            if isinstance(value, (int, float))
        }
        cards = self.retrieve(
            parent=parent,
            symptoms=symptoms,
            state_root=state_root,
            count=max(count * 2, count),
        )
        ranked = sorted(cards, key=lambda card: (counts.get(card.card_id, 0) > 5, counts.get(card.card_id, 0), card.card_id))
        return [
            {
                "card_id": card.card_id,
                # Paper cards are retrieval context, never an executable
                # mechanism menu.  Do not leak their source hooks, signals,
                # or patch-template prose into the Teacher prompt.
                "topic_tags": list(card.symptom_tags),
                "usage_count": counts.get(card.card_id, 0),
                "overuse_penalty": counts.get(card.card_id, 0) > 5,
            }
            for card in ranked[:count]
        ]

    def record_paper_card_references(
        self,
        *,
        state_root: Path,
        round_index: int,
        card_ids: Sequence[str],
    ) -> dict[str, int]:
        """Persist actual Teacher citations after a plan is structurally valid."""
        path = state_root / "knowledge" / "paper_card_usage.json"
        payload = load_json(path, {"schema_version": "goalevolve.v2.paper-card-usage.v1", "counts": {}, "rounds": []}) or {}
        counts = {
            str(card_id): int(value)
            for card_id, value in dict(payload.get("counts") or {}).items()
            if isinstance(value, (int, float))
        }
        unique = tuple(dict.fromkeys(str(card_id) for card_id in card_ids if str(card_id)))
        for card_id in unique:
            counts[card_id] = counts.get(card_id, 0) + 1
        rounds = list(payload.get("rounds") or [])
        rounds.append({"round": int(round_index), "card_ids": list(unique)})
        atomic_json(
            path,
            {
                "schema_version": "goalevolve.v2.paper-card-usage.v1",
                "counts": dict(sorted(counts.items())),
                "rounds": rounds[-500:],
            },
        )
        return counts

    def audit(self, *, state_root: Path) -> dict[str, object]:
        ledger = load_json(state_root / "knowledge" / "retrieval_ledger.json", {"rounds": []})
        rounds = list(ledger.get("rounds") or [])
        ids = [card for row in rounds for card in row.get("card_ids", [])]
        return {"round_count": len(rounds), "unique_card_count": len(set(ids)), "retrievals": len(ids), "ledger_hash": sha256_json(rounds)}


class DiversePlanner:
    name = "diverse_planner"

    def __init__(self, retriever: DiverseRetriever | None = None, *, scope_resolver=None) -> None:
        self.retriever = retriever or DiverseRetriever()
        self.scope_resolver = scope_resolver

    def plan(
        self,
        *,
        contract,
        parent: Parent,
        round_index: int,
        student_ids,
        state_root: Path,
        diagnosis=None,
        decision_context: Mapping[str, object] | None = None,
    ) -> list[Hypothesis]:
        _, residuals, _ = contract.evaluate(parent.metrics)
        stage = str((decision_context or {}).get("stage") or "")
        stage_patch_paths = tuple(
            str(path) for path in list((decision_context or {}).get("source_focus") or [])
        )
        evaluation_mode = str((decision_context or {}).get("evaluation_mode") or "timing_only")
        recipe_by_student = {
            str(student): str(recipe)
            for student, recipe in dict((decision_context or {}).get("timing_recipe_ids") or recipes_for_students(student_ids)).items()
        }
        champion_recipe = str(
            (decision_context or {}).get("execution_champion_recipe_id") or ""
        )
        # The scheduler must constrain retrieval as well as the LLM prompt.
        # Otherwise a timing-safe parent that still misses both power targets
        # can spend its Student slots on generic timing/route cards merely
        # because the frozen final contract contains TNS.
        if stage == "power_reclaim":
            symptoms = ["leakage", "dynamic", "power", "power_reclaim"]
        elif stage == "timing_recovery":
            symptoms = ["tns", "timing", "timing_recovery"]
        elif stage == "adaptive_tradeoff":
            symptoms = [
                "tns",
                "timing",
                "timing_recovery",
                "leakage",
                "dynamic",
                "power",
                "cell_reversion",
            ]
        else:
            symptoms = [name.split("_")[0] for name, residual in residuals.items() if residual and residual > 0]
        # A parent that already meets both power targets but misses only TNS
        # is not another generic timing experiment.  It is a timing-debt
        # recovery problem caused by a retained prior optimization.  Marking
        # this state explicitly lets evidence-backed recovery cards outrank
        # generic route cards that merely happen to mention "tns".
        active_metrics = {name for name, residual in residuals.items() if residual and residual > 0}
        if active_metrics == {"tns_abs_ns"}:
            symptoms.append("timing_recovery")
        responsible_stage = str(getattr(diagnosis, "responsible_stage", "")).lower()
        if stage != "power_reclaim" and "route" in responsible_stage:
            symptoms.extend(("route", "post_route"))
        # Retrieve a ranked pool before scope verification.  Asking for only
        # N cards and then falling back to an unverified hook makes a missing
        # source path look like an editable experiment; it also lets a stale
        # card crowd out a valid lower-ranked mechanism.  Scope resolution is
        # a hard admission gate, not an annotation.
        if stage == "adaptive_tradeoff":
            # Keep the Teacher's safety menu diverse after protected power
            # rounds. A single blended rank can hide upstream power durability
            # or power-to-timing reversion evidence under generic timing cards.
            # These are candidate-pool coverage groups, not Student roles.
            # The actual roster uses all Explorer slots when no EPD role is
            # eligible, otherwise it includes the eligible historical roles.
            timing_cards = self.retriever.retrieve(
                parent=parent,
                symptoms=("tns", "timing", "timing_recovery"),
                state_root=state_root,
                count=len(self.retriever.cards),
            )
            power_cards = self.retriever.retrieve(
                parent=parent,
                symptoms=("leakage", "dynamic", "power", "power_reclaim"),
                state_root=state_root,
                count=len(self.retriever.cards),
            )
            handoff_pool = self.retriever.retrieve(
                parent=parent,
                symptoms=("cell_reversion", "timing_power_rebuild"),
                state_root=state_root,
                count=len(self.retriever.cards),
            )
            handoff_cards = [
                card
                for card in handoff_pool
                if {"cell_reversion", "timing_power_rebuild"}.intersection(
                    tag.lower() for tag in card.symptom_tags
                )
            ]
            direct_timing_cards = [card for card in timing_cards if card not in handoff_cards]
            dominant = str((decision_context or {}).get("dominant_metric") or "")
            dominant_cards = power_cards if dominant in {"dynamic_power_pw", "leakage_power_pw"} else direct_timing_cards
            secondary_cards = direct_timing_cards if dominant_cards is power_cards else power_cards
            requested = [
                *dominant_cards[:2],
                *secondary_cards[:1],
                *handoff_cards[:1],
            ]
            # Retain evidence provenance for each coverage group but do not
            # force any group into a role slot. A missing group narrows the
            # Teacher menu instead of relabeling another mechanism as it.
            cards = []
            for card in requested:
                if card not in cards:
                    cards.append(card)
        else:
            cards = self.retriever.retrieve(parent=parent, symptoms=symptoms, state_root=state_root, count=len(self.retriever.cards))
        resolved: list[tuple[MechanismCard, object | None]] = []
        for card in cards:
            decision = self.scope_resolver.resolve(card) if self.scope_resolver else None
            if self.scope_resolver and (decision is None or not decision.files):
                continue
            resolved.append((card, decision))
        portfolio = EvolutionProgramDatabase(state_root).role_portfolio(
            contract=contract,
            parent=parent,
        )
        portfolio_by_id = {
            str(row.get("record_id") or ""): row
            for row in list(portfolio.get("records") or ())
            if isinstance(row, Mapping)
        }
        planned: list[Hypothesis] = []
        explorer_options: list[Hypothesis] = []
        for option_index, (card, decision) in enumerate(resolved):
            explorer_options.append(
                self._hypothesis_for_card(
                    card=card,
                    decision=decision,
                    student_id="",
                    role="explorer",
                    role_mode="fresh_exploration",
                    epd_records=(),
                    round_index=round_index,
                    stage=stage,
                    evaluation_mode=evaluation_mode,
                    recipe_by_student=recipe_by_student,
                    champion_recipe=champion_recipe,
                    stage_patch_paths=stage_patch_paths,
                )
            )
        pending_ideas = list(portfolio.get("pending_explorer_ideas") or ())
        for pending in pending_ideas:
            hooks = tuple(str(path) for path in list(pending.get("source_hooks") or ()) if path)
            signals = tuple(str(signal) for signal in list(pending.get("expected_signals") or ()) if signal)
            if not hooks or not self._epd_paths_exist(hooks):
                continue
            idea_id = str(pending.get("idea_id") or "")
            if not idea_id:
                continue
            pending_card = MechanismCard(
                card_id=f"epd_pending__{idea_id}",
                mechanism_family="epd_pending_explorer",
                symptom_tags=(),
                source_hooks=hooks,
                expected_signals=signals,
                claim_template=str(pending.get("idea") or ""),
                evidence_level="teacher_pending_idea",
                scope="epd_pending",
            )
            explorer_options.append(
                replace(
                    self._hypothesis_for_card(
                        card=pending_card,
                        decision=None,
                        student_id="",
                        role="explorer",
                        role_mode="pending_exploration",
                        epd_records=(),
                        round_index=round_index,
                        stage=stage,
                        evaluation_mode=evaluation_mode,
                        recipe_by_student=recipe_by_student,
                        champion_recipe=champion_recipe,
                        stage_patch_paths=stage_patch_paths,
                    ),
                    epd_idea_id=idea_id,
                )
            )
        # A teacher-controlled fresh menu gives explorers distinct ideas and
        # lets bootstrap roles seed future EPD mechanisms. It is not a source
        # parent and every selected option still receives an isolated flow.
        role_option_records = {
            "integrator": self._epd_option_records(
                portfolio=portfolio,
                portfolio_by_id=portfolio_by_id,
                candidate_key="integration_candidates",
                minimum_records=2,
            ),
            "enhancer": self._epd_option_records(
                portfolio=portfolio,
                portfolio_by_id=portfolio_by_id,
                candidate_key="enhancement_candidates",
                minimum_records=1,
            ),
        }
        eligible_roles = {
            role: tuple(
                records
                for records in options
                if self._epd_records_source_available(records)
            )
            for role, options in role_option_records.items()
        }
        # A historical role is meaningful only with executable EPD evidence.
        # Otherwise each configured worker performs an independent fresh
        # exploration, which keeps a four-Student campaign fully utilized.
        roles = self._roles_for_students(
            student_ids,
            has_integration=bool(eligible_roles["integrator"]),
            has_enhancement=bool(eligible_roles["enhancer"]),
        )
        # Fresh explorers consume distinct retrieved cards. EPD roles are
        # different: they are new, source-fenced experiments derived from
        # source-backed historical mechanisms, so they must not disappear merely
        # because a promoted original card is rightly absent from retrieval.
        fresh_index = 0
        for student_id, role in zip(student_ids, roles, strict=False):
            selectable_records = eligible_roles.get(role, ())
            if role in {"integrator", "enhancer"} and selectable_records:
                role_records = selectable_records[0]
                card = self._epd_mechanism_card(role=role, records=role_records)
                decision = None
            else:
                if fresh_index >= len(resolved):
                    continue
                card, decision = resolved[fresh_index]
                fresh_index += 1
                role_records = ()
            role_mode = self._role_mode(role=role, epd_records=role_records)
            candidate = self._hypothesis_for_card(
                card=card,
                decision=decision,
                student_id=str(student_id),
                role=role,
                role_mode=role_mode,
                epd_records=role_records,
                round_index=round_index,
                stage=stage,
                evaluation_mode=evaluation_mode,
                recipe_by_student=recipe_by_student,
                champion_recipe=champion_recipe,
                stage_patch_paths=stage_patch_paths,
            )
            if role == "explorer":
                candidate_options = tuple(
                    replace(option, student_id=str(student_id)).to_dict()
                    for option in explorer_options
                )
            elif selectable_records:
                candidate_options = tuple(
                    self._hypothesis_for_card(
                        card=self._epd_mechanism_card(role=role, records=records),
                        decision=None,
                        student_id=str(student_id),
                        role=role,
                        role_mode=self._role_mode(role=role, epd_records=records),
                        epd_records=records,
                        round_index=round_index,
                        stage=stage,
                        evaluation_mode=evaluation_mode,
                        recipe_by_student=recipe_by_student,
                        champion_recipe=champion_recipe,
                        stage_patch_paths=stage_patch_paths,
                    ).to_dict()
                    for records in selectable_records
                )
            else:
                candidate_options = tuple(
                    replace(option, student_id=str(student_id), student_role=role, role_mode=role_mode).to_dict()
                    for option in explorer_options
                )
            candidate = replace(candidate, candidate_options=candidate_options)
            planned.append(candidate)
        if not planned:
            raise RuntimeError("no source-verified fresh or EPD mechanisms remain for this round")
        return planned

    @staticmethod
    def _role_mode(*, role: str, epd_records: Sequence[Mapping[str, object]]) -> str:
        if role == "integrator":
            return "epd_integration" if epd_records else "bootstrap_integration"
        if role == "enhancer":
            return "epd_enhancement" if epd_records else "bootstrap_enhancement"
        return "fresh_exploration"

    @staticmethod
    def _epd_option_records(
        *,
        portfolio: Mapping[str, object],
        portfolio_by_id: Mapping[str, Mapping[str, object]],
        candidate_key: str,
        minimum_records: int,
    ) -> tuple[tuple[Mapping[str, object], ...], ...]:
        options: list[tuple[Mapping[str, object], ...]] = []
        for raw_ids in list(portfolio.get(candidate_key) or ()):
            record_ids = (raw_ids,) if isinstance(raw_ids, str) else tuple(raw_ids or ())
            records = tuple(
                portfolio_by_id[str(record_id)]
                for record_id in record_ids
                if str(record_id) in portfolio_by_id
            )
            if len(records) >= minimum_records and records not in options:
                options.append(records)
        return tuple(options)

    def _epd_records_source_available(
        self,
        records: Sequence[Mapping[str, object]],
    ) -> bool:
        hooks = tuple(
            dict.fromkeys(
                str(path)
                for record in records
                for path in list(record.get("source_hooks") or ())
                if path
            )
        )
        return bool(hooks) and self._epd_paths_exist(hooks)

    @staticmethod
    def _epd_mechanism_card(
        *,
        role: str,
        records: Sequence[Mapping[str, object]],
    ) -> MechanismCard:
        """Represent a source-backed, non-invalid EPD selection without replaying its old card."""
        record_ids = tuple(str(record.get("record_id") or "") for record in records)
        hooks = tuple(
            dict.fromkeys(
                str(path)
                for record in records
                for path in list(record.get("source_hooks") or ())
                if path
            )
        )
        signals = tuple(
            dict.fromkeys(
                str(signal)
                for record in records
                for signal in list(record.get("expected_signals") or ())
                if signal
            )
        )
        families = tuple(
            dict.fromkeys(
                str(record.get("mechanism_family") or "mechanism")
                for record in records
            )
        )
        action = "Combine" if role == "integrator" else "Refine"
        return MechanismCard(
            card_id=f"epd_{role}__{'__'.join(record_ids)}",
            mechanism_family=f"epd_{role}",
            symptom_tags=(),
            source_hooks=hooks,
            expected_signals=signals,
            claim_template=(
                f"{action} the source-backed non-invalid EPD mechanisms ({', '.join(families)}) "
                "through one bounded source change with fresh evidence."
            ),
            evidence_level="noninvalid_epd",
            scope="epd_history",
        )

    def _hypothesis_for_card(
        self,
        *,
        card: MechanismCard,
        decision: object | None,
        student_id: str,
        role: str,
        role_mode: str,
        epd_records: Sequence[Mapping[str, object]],
        round_index: int,
        stage: str,
        evaluation_mode: str,
        recipe_by_student: Mapping[str, str],
        champion_recipe: str,
        stage_patch_paths: Sequence[str],
        candidate_suffix: str = "",
    ) -> Hypothesis:
        recipe_id = champion_recipe if stage == "adaptive_tradeoff" and champion_recipe else recipe_by_student.get(student_id, "legacy_setup")
        if stage == "power_reclaim" and card.card_id == "repair_power_rmp_area_recipe_v1":
            recipe_id = "rmp_area_power"
        if stage in {"timing_recovery", "adaptive_tradeoff"}:
            card_key = f"{card.card_id} {card.mechanism_family}".lower()
            if "legacy_mt" in card_key:
                recipe_id = "legacy_mt"
            elif "wns_cone" in card_key:
                recipe_id = "wns_cone"
            elif "reroute" in card_key:
                recipe_id = "reroute_mid_power"
            elif "measured_critical" in card_key or "measured_crit" in card_key:
                recipe_id = "measured_critical_path_deep"
            elif "measured_vt" in card_key:
                recipe_id = "measured_vt_deep"
            elif "last_gasp" in card_key:
                recipe_id = "last_gasp_deep"
            elif "crit_vt" in card_key:
                recipe_id = "crit_vt_deep"
            elif "mt1" in card_key:
                recipe_id = "mt1_deep"
            elif any(token in card_key for token in ("power_stability", "timing_power_rebuild", "compound_vt_reserve", "compound_vt_critical_halo", "compound_vt_critical_fanin_halo", "pure_vt_critical_halo", "size_down_critical_halo", "buffer_removal_critical_halo", "compound_vt_timing_reserve")):
                recipe_id = "mt1_deep"
            elif "rmp" in card_key or "restructure" in card_key:
                recipe_id = "rmp_post_timing_halo" if "post_timing_halo" in card_key or "post_timing_wire" in card_key else ("rmp_path_cone_halo_timing" if "path_cone_halo_v5" in card_key else ("rmp_path_cone_timing" if "path_cone_v4" in card_key else "rmp_delay_timing"))
            elif "wns_path" in card_key:
                recipe_id = "wns_path_deep"
            elif "wns" in card_key:
                recipe_id = "wns_cone"
            elif "legacy" in card_key:
                recipe_id = "legacy_deep"
            elif "tns_" in card_key or "_tns" in card_key:
                recipe_id = "tns_global"
        hooks = tuple(getattr(decision, "files", ()) or card.source_hooks)
        if epd_records:
            epd_hooks = tuple(dict.fromkeys(str(path) for record in epd_records for path in list(record.get("source_hooks") or ()) if path))
            if epd_hooks and self._epd_paths_exist(epd_hooks):
                hooks = epd_hooks
        epd_signals = tuple(dict.fromkeys(str(signal) for record in epd_records for signal in list(record.get("expected_signals") or ()) if signal))
        scope_evidence = () if decision is None else (
            f"scope_confidence:{getattr(decision, 'confidence', 'declared_only')}",
            *(f"metric:{item}" for item in getattr(decision, "observed_metrics", ())),
            *(f"symbol:{item}" for item in getattr(decision, "observed_symbols", ())),
        )
        exact_stage_paths = tuple(path for path in stage_patch_paths if path.endswith((".cc", ".hh", ".cpp", ".hpp", ".h", ".tcl")))
        allowed_paths = tuple(path for path in hooks if path.endswith((".cc", ".hh", ".cpp", ".hpp", ".h", ".tcl"))) if epd_records else (exact_stage_paths or tuple(path for path in hooks if path.endswith((".cc", ".hh", ".cpp", ".hpp", ".h", ".tcl"))))
        claim = card.claim_template
        if role_mode == "epd_integration":
            claim = "Integrate only compatible source-backed decisions from the selected EPD mechanisms; preserve guards, rollback, and telemetry semantics. " + claim
        elif role_mode == "epd_enhancement":
            claim = "Enhance the selected source-backed EPD mechanism with one bounded refinement; preserve its mechanism boundary. " + claim
        identity = student_id or "candidate"
        return Hypothesis(
            hypothesis_id=f"r{round_index}_{identity}_{card.card_id}{'__' + candidate_suffix if candidate_suffix else ''}",
            mechanism_family=card.mechanism_family,
            claim=claim,
            source_hooks=hooks,
            expected_signals=epd_signals or card.expected_signals,
            retrieval_ids=(card.card_id,),
            novelty_key=f"{role}:{card.mechanism_family}:{'|'.join(hooks)}:{'|'.join(str(record.get('record_id') or '') for record in epd_records)}",
            scope_evidence=scope_evidence,
            allowed_patch_paths=allowed_paths,
            evaluation_mode=evaluation_mode,
            timing_recipe_id=recipe_id,
            activation_signals=card.activation_signals,
            conclusive_nonactivation_patterns=card.conclusive_nonactivation_patterns,
            student_role=role,
            role_mode=role_mode,
            epd_record_ids=tuple(str(record.get("record_id") or "") for record in epd_records),
            epd_idea_id=(
                str(epd_records[0].get("idea_id") or "")
                if role == "enhancer" and role_mode == "epd_enhancement" and len(epd_records) == 1
                else ""
            ),
            student_id=student_id,
        )

    @staticmethod
    def _roles_for_students(
        student_ids: Sequence[str],
        *,
        has_integration: bool,
        has_enhancement: bool,
    ) -> tuple[str, ...]:
        """Schedule historical roles only when their EPD evidence is executable."""
        ids = tuple(student_ids)
        if not has_integration and not has_enhancement:
            return tuple("explorer" for _ in ids)
        roles = ["explorer", "explorer"]
        if has_integration:
            roles.append("integrator")
        if has_enhancement:
            roles.append("enhancer")
        return tuple(roles[: len(ids)])

    def _epd_paths_exist(self, paths: Sequence[str]) -> bool:
        if self.scope_resolver is None or not getattr(self.scope_resolver, "index", None):
            return True
        source_index = getattr(self.scope_resolver, "index")
        return all(str(path) in source_index for path in paths)

    def commit_round(
        self,
        *,
        contract,
        parent: Parent,
        round_index: int,
        state_root: Path,
        diagnosis=None,
        hypotheses: Sequence[Hypothesis],
        decision_context: Mapping[str, object] | None = None,
    ) -> None:
        """Persist retrieval only after the round has complete evidence.

        Planning is speculative.  Recording it immediately made an interrupted
        process look like a completed negative experiment, suppressing a card
        before any Student had emitted evidence.  The ledger is therefore a
        completed-round provenance record, not a queue journal.
        """
        _, residuals, _ = contract.evaluate(parent.metrics)
        stage = str((decision_context or {}).get("stage") or "")
        if stage == "power_reclaim":
            symptoms = ["leakage", "dynamic", "power", "power_reclaim"]
        elif stage == "timing_recovery":
            symptoms = ["tns", "timing", "timing_recovery"]
        elif stage == "adaptive_tradeoff":
            symptoms = ["tns", "timing", "leakage", "dynamic", "power", "cell_reversion"]
        else:
            symptoms = [name.split("_")[0] for name, residual in residuals.items() if residual and residual > 0]
        if stage != "power_reclaim" and "route" in str(getattr(diagnosis, "responsible_stage", "")).lower():
            symptoms.extend(("route", "post_route"))
        by_id = {card.card_id: card for card in self.retriever.cards}
        cards = {
            identifier: by_id[identifier]
            for hypothesis in hypotheses
            for identifier in hypothesis.retrieval_ids
            if identifier in by_id
        }
        if cards:
            self.retriever.record(
                state_root=state_root,
                round_index=round_index,
                cards=list(cards.values()),
                symptoms=symptoms,
            )


class RoundRobinPlanner:
    """Retrieval-free ablation with the same card schema and fresh Explorer roles."""

    name = "round_robin_planner"

    def __init__(self, cards: Iterable[MechanismCard] = DEFAULT_CARDS) -> None:
        self.cards = tuple(cards)

    def plan(self, *, contract, parent: Parent, round_index: int, student_ids, state_root: Path, diagnosis=None, decision_context: Mapping[str, object] | None = None) -> list[Hypothesis]:
        if len(self.cards) < len(student_ids):
            raise ValueError("round-robin ablation needs at least one card per student")
        offset = (round_index - 1) % len(self.cards)
        cards = [self.cards[(offset + index) % len(self.cards)] for index in range(len(student_ids))]
        roles = DiversePlanner._roles_for_students(
            student_ids,
            has_integration=False,
            has_enhancement=False,
        )
        return [
            Hypothesis(
                hypothesis_id=f"r{round_index}_{student_id}_{card.card_id}",
                mechanism_family=card.mechanism_family,
                claim=card.claim_template,
                source_hooks=card.source_hooks,
                expected_signals=card.expected_signals,
                retrieval_ids=(card.card_id,),
                novelty_key=f"{card.mechanism_family}:{'|'.join(card.source_hooks)}",
                student_role=role,
                role_mode="fresh_exploration",
                student_id=str(student_id),
            )
            for student_id, card, role in zip(student_ids, cards, roles, strict=True)
        ]
