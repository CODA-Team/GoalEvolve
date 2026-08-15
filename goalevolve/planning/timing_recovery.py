"""Auditable timing-recovery schedules and their persistent experiment memory.

This module owns the *controller-side* Tcl choices used after the promoted
low-power reconstruction.  Students may evolve the executed C++ policy, but
cannot silently turn a source experiment into an unrecorded Tcl sweep.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..core.io import atomic_json, load_json


@dataclass(frozen=True)
class TimingRecoveryRecipe:
    recipe_id: str
    description: str
    phases: tuple[str, ...] = ()
    sequence: tuple[str, ...] = ()
    repair_tns_percent: int | None = None
    max_passes: int | None = None
    max_iterations: int | None = None
    max_repairs_per_pass: int | None = None
    insert_mid_power: bool = False
    enable_tns_endpoint_frontier: bool = False
    # RMP is a separate combinational-resynthesis engine.  Keep this explicit
    # so a timing experiment cannot silently turn into a Tcl-side restructure
    # sweep, and so its no-diff baseline has exactly the same invocation.
    insert_rmp_delay_restructure: bool = False
    rmp_endpoint_path_count: int = 1
    rmp_max_tried_clouds: int = 1
    rmp_before_timing: bool = False
    # A path-cone controller is materially different from independent endpoint
    # clouds: it unions a bounded number of paths for one endpoint and adds a
    # bounded upstream fanin halo before RMP sees the cone.  Keep these limits
    # recipe-owned so the matching no-diff baseline is exact.
    rmp_union_endpoint_paths: bool = False
    rmp_expand_side_fanin_levels: int = 0
    rmp_expand_side_fanin_max_add: int = 0
    # Never permit the generic all-fanin blob to silently replace a failed
    # path-cone experiment. This is only meaningful for a controller that
    # explicitly studies path-cone construction.
    rmp_path_cone_only: bool = False
    # Area-oriented RMP is an upstream power experiment.  It shares the
    # recipe identity/baseline machinery but never implies repair_timing.
    insert_rmp_area_restructure: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def repair_timing_command(self) -> str:
        parts = ["repair_timing", "-setup"]
        if self.phases:
            parts.extend(("-phases", "{" + " ".join(self.phases) + "}"))
        if self.sequence:
            parts.extend(("-sequence", "{" + " ".join(self.sequence) + "}"))
        if self.repair_tns_percent is not None:
            parts.extend(("-repair_tns", str(self.repair_tns_percent)))
        if self.max_repairs_per_pass is not None:
            parts.extend(("-max_repairs_per_pass", str(self.max_repairs_per_pass)))
        if self.max_passes is not None:
            parts.extend(("-max_passes", str(self.max_passes)))
        if self.max_iterations is not None:
            parts.extend(("-max_iterations", str(self.max_iterations)))
        return " ".join(parts)

    def environment_commands(self) -> tuple[str, ...]:
        """Return the explicit, recipe-owned feature activations for Tcl.

        Source experiments must not depend on an inherited shell environment:
        the command that selects a policy also records every source feature it
        activates.  This makes a no-diff recipe baseline a valid comparison.
        """
        if self.enable_tns_endpoint_frontier:
            # The recipe-owned spelling is primary.  Keep the legacy spelling
            # during the source migration so an older parent and a repaired
            # policy are evaluated under the same explicit controller intent.
            return (
                "set ::env(RSZ_ENABLE_TNS_ENDPOINT_FRONTIER) 1",
                "set ::env(RSZ_TNS_ACCEPT_ENDPOINT_IMPROVE) 1",
            )
        return ()


# These are deliberately bounded, source-compatible probes distilled from the
# whitebox command builder.  Every ID gets an independently measured parent
# baseline before it can contribute source-attributed evidence.
TIMING_RECOVERY_RECIPES = {
    "legacy_setup": TimingRecoveryRecipe(
        "legacy_setup", "Existing repair_timing -setup control; compatibility baseline."
    ),
    "implicit_power_recovery_plus": TimingRecoveryRecipe(
        "implicit_power_recovery_plus",
        "Default repair_timing phases, preserving the empty phase list that dispatches the controller-owned implicit power-recovery-plus experiment.",
    ),
    "rmp_area_power": TimingRecoveryRecipe(
        "rmp_area_power",
        "Bounded controller-owned RMP target=area after repair_power; no repair_timing pass.",
        insert_rmp_area_restructure=True,
    ),
    "legacy_mt": TimingRecoveryRecipe(
        "legacy_mt", "Explore the experimental multi-thread legacy policy with a bounded final tail.",
        ("LEGACY_MT", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match"),
        20, 1, 1, 1,
    ),
    "tns_global": TimingRecoveryRecipe(
        "tns_global", "Distributed negative slack: TNS-first global recovery.",
        ("TNS", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 1, 1, 1, False, True,
    ),
    "wns_cone": TimingRecoveryRecipe(
        "wns_cone", "Concentrate on the current worst endpoint/cone before the bounded tail.",
        ("WNS_CONE", "CRIT_VT_SWAP", "LAST_GASP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match"),
        0, 1, 1, 1,
    ),
    "wns_path_deep": TimingRecoveryRecipe(
        "wns_path_deep",
        "Two-pass WNS-path repair for a localized data-path bottleneck.",
        ("WNS_PATH", "WNS_CONE", "CRIT_VT_SWAP", "LAST_GASP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        20, 2, 2, 2,
    ),
    "crit_vt_deep": TimingRecoveryRecipe(
        "crit_vt_deep",
        "Two-pass critical-VT recovery with a small TNS cleanup tail.",
        ("CRIT_VT_SWAP", "TNS", "LAST_GASP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        30, 2, 2, 2,
    ),
    "reroute_mid_power": TimingRecoveryRecipe(
        "reroute_mid_power", "Route-delay probe with a bounded mid-area power reclaim between timing passes.",
        ("REROUTE", "TNS", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        30, 1, 1, 1, True,
    ),
    # R27 deliberately used one-move activation probes.  Once a probe has
    # established that a timing-debt parent needs actual recovery capacity,
    # use a separate, still bounded, two-pass schedule.  The AES whitebox
    # guards cap both passes and repairs-per-pass at two; these recipes obey
    # that guard rather than making the controller silently unbounded.
    "legacy_deep": TimingRecoveryRecipe(
        "legacy_deep", "Two-pass stable legacy recovery after a low-power rebuild.",
        ("LEGACY", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2,
    ),
    "last_gasp_deep": TimingRecoveryRecipe(
        "last_gasp_deep", "Two-pass late timing cleanup with explicit critical-VT refinement.",
        ("LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2,
    ),
    "measured_vt_deep": TimingRecoveryRecipe(
        "measured_vt_deep", "Two-pass measured-VT recovery followed by bounded late cleanup.",
        ("MEASURED_VT_SWAP", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2,
    ),
    "measured_critical_path_deep": TimingRecoveryRecipe(
        "measured_critical_path_deep",
        "Measured critical-path global-TNS recovery followed by bounded cleanup.",
        ("MEASURED_CRIT_PATH", "TNS", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2,
    ),
    "mt1_deep": TimingRecoveryRecipe(
        "mt1_deep", "Two-pass MT1/TNS recovery with a bounded late cleanup tail.",
        ("MT1", "TNS", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2,
    ),
    "rmp_delay_timing": TimingRecoveryRecipe(
        "rmp_delay_timing",
        "Bounded multi-endpoint RMP delay restructure before timing recovery.",
        ("MT1", "TNS", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2, False, False, True, 4, 4, True,
    ),
    "rmp_path_cone_timing": TimingRecoveryRecipe(
        "rmp_path_cone_timing",
        "Bounded unioned endpoint path-cone RMP before timing recovery.",
        ("MT1", "TNS", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2, False, False, True, 4, 4, True,
        True, 1, 16,
    ),
    "rmp_path_cone_halo_timing": TimingRecoveryRecipe(
        "rmp_path_cone_halo_timing",
        "Bounded post-halo path-cone RMP without generic-fanin fallback.",
        ("MT1", "TNS", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2, False, False, True, 4, 4, True,
        True, 1, 16, True,
    ),
    "rmp_post_timing_halo": TimingRecoveryRecipe(
        "rmp_post_timing_halo",
        "Bounded two-level path-cone RMP after the first MT1/TNS timing recovery.",
        ("MT1", "TNS", "LAST_GASP", "CRIT_VT_SWAP"),
        ("vt_swap", "sizeup", "swap", "sizeup_match", "buffer"),
        40, 2, 2, 2, False, False, True, 4, 4, False,
        True, 2, 24, True,
    ),
}


def timing_recipe(recipe_id: str | None) -> TimingRecoveryRecipe:
    key = recipe_id or "legacy_setup"
    if key not in TIMING_RECOVERY_RECIPES:
        raise ValueError(f"unknown timing recovery recipe: {key}")
    return TIMING_RECOVERY_RECIPES[key]


# These names are existing OpenROAD policy entry points, not mechanism
# categories.  A Teacher remains free to author any bounded decision inside a
# policy, while the Controller must run a recipe that actually dispatches the
# edited policy before it can collect causal evidence.
_PHASE_BOUND_POLICY_RECIPES: dict[str, tuple[str, ...]] = {
    "SetupMt1Policy": (
        "mt1_deep",
        "rmp_delay_timing",
        "rmp_path_cone_timing",
        "rmp_path_cone_halo_timing",
        "rmp_post_timing_halo",
    ),
    "MeasuredCriticalPathPolicy": ("measured_critical_path_deep",),
    "MeasuredVtSwapPolicy": ("measured_vt_deep",),
    "SetupLegacyMtPolicy": ("legacy_mt",),
    "SetupLastGaspPolicy": ("last_gasp_deep",),
    "SetupCritVtSwapPolicy": ("crit_vt_deep",),
    "SetupReroutePolicy": ("reroute_mid_power",),
    "SetupTnsPolicy": (
        "tns_global",
        "rmp_delay_timing",
        "rmp_path_cone_timing",
        "rmp_path_cone_halo_timing",
        "rmp_post_timing_halo",
    ),
    "SetupWnsPolicy": ("wns_path_deep", "wns_cone"),
}

# Generic source files do not expose one policy class that can be inferred
# from their basename.  They still need a bounded Controller recipe whenever a
# Teacher says the source decision is meaningful only under an RMP schedule.
_SOURCE_BOUND_RECIPES: dict[str, tuple[str, ...]] = {
    "src/rmp/src/Restructure.cpp": (
        "rmp_delay_timing",
        "rmp_path_cone_timing",
        "rmp_path_cone_halo_timing",
        "rmp_post_timing_halo",
        "rmp_area_power",
    ),
}

# Some policy classes are selected by a controller *mode*, rather than a
# repair_timing recipe.  Keep these separate from the recipe mapping above:
# ``power_only`` and ``power_then_timing`` both dispatch ``REPAIR_POWER``
# before their optional downstream work, while ``timing_only`` never does.
# ``POWER_RECOVERY_PLUS`` has a source implementation but no controller-owned
# recipe currently invokes it, so a Teacher cannot attribute an experiment to
# that policy yet.
_SOURCE_BOUND_EVALUATION_MODES: dict[str, tuple[str, ...]] = {
    "src/rsz/src/policy/RepairPowerPolicy.cc": (
        "power_only",
        "power_then_timing",
    ),
    "src/rsz/src/policy/PowerRecoveryPlusPolicy.cc": (),
}


def teacher_selectable_recipe_ids(evaluation_mode: str) -> tuple[str, ...]:
    """Return controller-owned schedules that a Teacher may name in Markdown.

    This is a closed menu, not permission to create Tcl.  The selected ID is
    later checked against the source hook and used unchanged for the candidate
    and its no-diff parent baseline.
    """
    if evaluation_mode == "power_only":
        return tuple(
            recipe_id
            for recipe_id, recipe in TIMING_RECOVERY_RECIPES.items()
            if recipe.insert_rmp_area_restructure or recipe_id == "legacy_setup"
        )
    return tuple(
        recipe_id
        for recipe_id, recipe in TIMING_RECOVERY_RECIPES.items()
        if not recipe.insert_rmp_area_restructure
    )


def recipe_is_compatible_with_source_hooks(
    recipe_id: str,
    source_hooks: Sequence[str],
    *,
    evaluation_mode: str | None = None,
) -> bool:
    """Whether a declared controller envelope reaches every constrained hook."""
    if recipe_id not in TIMING_RECOVERY_RECIPES:
        return False
    for hook in source_hooks:
        modes = _SOURCE_BOUND_EVALUATION_MODES.get(str(hook).replace("\\", "/"))
        if modes is not None and (evaluation_mode is None or evaluation_mode not in modes):
            return False
    required_sets = _required_recipe_sets(source_hooks)
    return all(recipe_id in choices for choices in required_sets)


def _required_recipe_sets(source_hooks: Sequence[str]) -> list[tuple[str, ...]]:
    required_sets: list[tuple[str, ...]] = []
    for hook in source_hooks:
        normalized = str(hook).replace("\\", "/")
        if normalized in _SOURCE_BOUND_RECIPES:
            required_sets.append(_SOURCE_BOUND_RECIPES[normalized])
        policy = normalized.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if policy in _PHASE_BOUND_POLICY_RECIPES:
            required_sets.append(_PHASE_BOUND_POLICY_RECIPES[policy])
    return required_sets


def recipe_for_source_hooks(
    source_hooks: Sequence[str],
    fallback_recipe_id: str | None,
) -> str:
    """Return a controller recipe that reaches every phase-bound policy hook.

    Generic helpers are intentionally left on the scheduler's fallback.  For
    a direct policy file, retaining an incompatible recipe would make a full
    flow look like a valid source experiment even though the changed code
    never ran.  If several direct policy hooks are incompatible with one
    another, preserve the supplied recipe so Controller admission can reject
    the assignment rather than silently inventing a combined Tcl schedule.
    """
    fallback = timing_recipe(fallback_recipe_id).recipe_id
    required_sets = _required_recipe_sets(source_hooks)
    if not required_sets:
        return fallback
    compatible = [
        recipe_id
        for recipe_id, recipe in TIMING_RECOVERY_RECIPES.items()
        if all(recipe_id in choices for choices in required_sets)
    ]
    if not compatible:
        return fallback
    return fallback if fallback in compatible else compatible[0]


def recipes_for_students(student_ids: Iterable[str]) -> dict[str, str]:
    """Assign diverse controller-owned probes for otherwise unbound slots.

    A source card that names a concrete policy later overrides this fallback.
    This is intentionally a *schedule allocation*, not an invitation for a
    Student to change Tcl: every allocated recipe is independently baselined.
    """
    order = ("legacy_deep", "wns_path_deep", "measured_vt_deep", "mt1_deep")
    return {student_id: order[index % len(order)] for index, student_id in enumerate(student_ids)}


def schedule_memory_summary(state_root: Path, *, limit: int = 8) -> dict[str, object]:
    payload = load_json(state_root / "knowledge" / "timing_schedule_memory.json", {"records": []})
    records = list(dict(payload or {}).get("records") or [])
    rollup: dict[str, dict[str, Any]] = {}
    for raw in records:
        record = dict(raw) if isinstance(raw, Mapping) else {}
        recipe_id = str(record.get("recipe_id") or "legacy_setup")
        bucket = rollup.setdefault(
            recipe_id,
            {
                "samples": 0,
                "activated_samples": 0,
                "verified_samples": 0,
                "best_goal_distance": None,
                "best_tns_abs_ns": None,
                "mean_power_overlap_rate": 0.0,
                "mean_exact_reversion_rate": 0.0,
                "overlap_samples": 0,
            },
        )
        bucket["samples"] += 1
        verdict = dict(record.get("verdict") or {})
        if verdict.get("mechanism_fired") is True:
            bucket["activated_samples"] += 1
        if str(verdict.get("state") or "") in {"validated", "verified_qor_unattributed"}:
            bucket["verified_samples"] += 1
        for metric, target in (("goal_distance", "best_goal_distance"),):
            value = _finite_number(verdict.get(metric))
            if value is not None and (bucket[target] is None or value < bucket[target]):
                bucket[target] = value
        metrics = dict(record.get("metrics") or {})
        tns = _finite_number(metrics.get("tns_abs_ns"))
        if tns is not None and (bucket["best_tns_abs_ns"] is None or tns < bucket["best_tns_abs_ns"]):
            bucket["best_tns_abs_ns"] = tns
        tradeoff = dict(record.get("cell_tradeoff") or {})
        overlap = _finite_number(tradeoff.get("overlap_rate_of_power_replacements"))
        reversion = _finite_number(tradeoff.get("exact_reversion_rate_of_power_replacements"))
        if overlap is not None or reversion is not None:
            bucket["overlap_samples"] += 1
            bucket["mean_power_overlap_rate"] += overlap or 0.0
            bucket["mean_exact_reversion_rate"] += reversion or 0.0
    for bucket in rollup.values():
        samples = int(bucket["overlap_samples"])
        if samples:
            bucket["mean_power_overlap_rate"] /= samples
            bucket["mean_exact_reversion_rate"] /= samples
        else:
            bucket.pop("mean_power_overlap_rate")
            bucket.pop("mean_exact_reversion_rate")
        bucket.pop("overlap_samples")

    # Rank only by outcome evidence.  Runtime never enters this recommendation.
    recommended = sorted(
        rollup,
        key=lambda recipe: (
            -int(rollup[recipe]["verified_samples"]),
            -int(rollup[recipe]["activated_samples"]),
            float(rollup[recipe]["best_goal_distance"] if rollup[recipe]["best_goal_distance"] is not None else float("inf")),
            float(rollup[recipe]["best_tns_abs_ns"] if rollup[recipe]["best_tns_abs_ns"] is not None else float("inf")),
            recipe,
        ),
    )
    def compact_recent(raw: object) -> dict[str, object]:
        """Keep scheduling evidence decision-sized, not instance-list-sized."""
        record = dict(raw) if isinstance(raw, Mapping) else {}
        metrics = dict(record.get("metrics") or {})
        verdict = dict(record.get("verdict") or {})
        tradeoff = dict(record.get("cell_tradeoff") or {})
        return {
            "round": record.get("round"),
            "student_id": record.get("student_id"),
            "hypothesis_id": record.get("hypothesis_id"),
            "recipe_id": record.get("recipe_id"),
            "verdict": {
                key: verdict.get(key)
                for key in ("state", "goal_distance", "distance_gain", "mechanism_fired", "integrity_ok")
            },
            "metrics": {
                key: metrics.get(key)
                for key in ("tns_abs_ns", "dynamic_power_pw", "leakage_power_pw", "drv_count")
            },
            "cell_tradeoff": {
                key: tradeoff.get(key)
                for key in (
                    "available",
                    "power_reclaim_available",
                    "timing_interaction_available",
                    "power_reclaim_replacements",
                    "timing_repair_replacements",
                    "power_to_timing_overlap",
                    "overlap_rate_of_power_replacements",
                    "exact_power_to_timing_reversions",
                    "exact_reversion_rate_of_power_replacements",
                    "power_transition_top",
                    "timing_transition_top",
                )
            },
        }

    return {
        "record_count": len(records),
        "recipe_rollup": rollup,
        "recommended_recipe_ids": recommended,
        "recent": [compact_recent(record) for record in records[-limit:]],
    }


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def record_schedule_memory(state_root: Path, records: list[dict[str, object]]) -> None:
    path = state_root / "knowledge" / "timing_schedule_memory.json"
    payload = load_json(path, {"schema_version": "goalevolve.v2.timing-schedule-memory.v1", "records": []})
    prior = list(dict(payload or {}).get("records") or [])
    atomic_json(path, {"schema_version": "goalevolve.v2.timing-schedule-memory.v1", "records": [*prior, *records]})
    # Keep the raw records immutable and append-only, while exposing a compact
    # durable view for a human or Teacher to use without reparsing all rounds.
    atomic_json(
        path.with_name("timing_schedule_summary.json"),
        schedule_memory_summary(state_root),
    )
