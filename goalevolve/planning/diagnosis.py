from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from ..core.contracts import GoalContract
from ..core.models import Parent


@dataclass(frozen=True)
class Diagnosis:
    dominant_bottleneck: str
    dominant_residual: float
    residuals: dict[str, float | None]
    responsible_stage: str
    checkpoint_effects: dict[str, dict[str, float]]
    checkpoint_effect_semantics: dict[str, str]
    baseline_policy: str
    unresolved_debt: dict[str, float]
    parent_goal_distance: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def diagnose(*, contract: GoalContract, parent: Parent, checkpoints: Mapping[str, Mapping[str, float]] | None) -> Diagnosis:
    """Convert frozen-goal residuals and checkpoint trajectory into a Teacher input.

    The responsible stage is inferred from the greatest improvement/degradation
    of the dominant metric. A final routing regression is represented as debt
    rather than silently erasing the preceding local effect.
    """
    _, residuals, _ = contract.evaluate(parent.metrics)
    weighted = [
        (spec.weight * float(residuals.get(spec.name) or 0.0), spec.name)
        for spec in contract.metrics
    ]
    dominant_residual, dominant = max(weighted, default=(0.0, contract.metrics[0].name))
    raw_trajectory = {
        str(stage): {str(name): float(value) for name, value in values.items()}
        for stage, values in dict(checkpoints or {}).items()
    }
    stage_order = (
        "pre_repair",
        "post_repair_design",
        "post_repair_power",
        "post_placement",
        "post_repair_timing_pre_mid_power",
        "post_repair_power_mid",
        "post_repair_timing_pre_rmp",
        "post_rmp_restructure_pre_timing",
        "post_rmp_restructure",
        "post_repair_timing",
        "post_route",
    )
    ordered_stages = [stage for stage in stage_order if stage in raw_trajectory]
    ordered_stages.extend(sorted(set(raw_trajectory) - set(ordered_stages)))
    trajectory = {stage: raw_trajectory[stage] for stage in ordered_stages}
    effects: dict[str, dict[str, float]] = {}
    previous: dict[str, float] = {}
    for stage, metrics in trajectory.items():
        effects[stage] = {}
        for spec in contract.metrics:
            before = previous.get(spec.name)
            after = metrics.get(spec.name)
            if before is None and after is not None:
                effects[stage][spec.name] = 0.0
            elif before is not None and after is not None:
                effects[stage][spec.name] = (float(before) - float(after)) if spec.minimize else (float(after) - float(before))
        previous.update(metrics)
    owner = "final_flow"
    if effects:
        # For a minimized metric an effect below zero is a stage regression.
        # The earlier implementation selected the largest absolute movement,
        # which can incorrectly blame an upstream improvement merely because
        # the fixed parent is measured after routing.  Prioritize the largest
        # observed regression; only fall back to magnitude when no stage lost
        # quality at all.
        regressions = [
            (float(values.get(dominant, 0.0)), stage)
            for stage, values in effects.items()
            if float(values.get(dominant, 0.0)) < 0.0
        ]
        if regressions:
            owner = min(regressions)[1]
        else:
            owner = max(effects, key=lambda stage: abs(effects[stage].get(dominant, 0.0)))
    debt: dict[str, float] = {}
    if trajectory:
        best = min((metrics.get(dominant, parent.metrics.get(dominant, 0.0)) for metrics in trajectory.values()), default=parent.metrics.get(dominant, 0.0))
        final = list(trajectory.values())[-1].get(dominant, parent.metrics.get(dominant, 0.0))
        if best is not None and final is not None:
            debt[dominant] = max(0.0, float(final) - float(best))
    return Diagnosis(
        dominant_bottleneck=dominant,
        dominant_residual=float(dominant_residual),
        residuals=residuals,
        responsible_stage=owner,
        checkpoint_effects=effects,
        checkpoint_effect_semantics={
            "formula_for_minimized_metrics": "before - after",
            "positive": "stage improved the metric",
            "negative": "stage regressed the metric",
            "zero": "no measured change",
        },
        baseline_policy=(
            "Within the same evaluation mode, compare candidates directly with the current "
            "parent's complete official metrics; do not rerun a no-diff baseline. A matched "
            "baseline is needed only when the evaluation stage or timing recipe actually changes."
        ),
        unresolved_debt=debt,
        parent_goal_distance=parent.goal_distance,
    )
