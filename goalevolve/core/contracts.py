from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .io import sha256_json


# Recorded for experiment auditing only.  Machine load and contest summary
# formulas must never become apparent source-quality signals.
OBSERVER_ONLY_METRICS = frozenset(
    {"runtime_s", "tool_runtime", "tool_runtime_s", "flow_runtime", "flow_runtime_s", "sfinal", "sppa"}
)


@dataclass(frozen=True)
class MetricSpec:
    """One frozen target. `minimize=True` means lower values are better."""

    name: str
    target: float
    baseline: float
    weight: float = 1.0
    minimize: bool = True
    hard: bool = False
    tolerance: float = 0.0

    def residual(self, value: float | None) -> float | None:
        if value is None:
            return None
        scale = max(abs(self.baseline - self.target), abs(self.baseline), 1.0)
        if self.minimize:
            raw = (float(value) - self.target - self.tolerance) / scale
        else:
            raw = (self.target - float(value) - self.tolerance) / scale
        return max(0.0, raw)


@dataclass(frozen=True)
class GoalContract:
    """Frozen per-campaign target set; never mutated by a later round."""

    design: str
    baseline_metrics: dict[str, float]
    metrics: tuple[MetricSpec, ...]
    source_fingerprint: dict[str, str]
    schema_version: str = "goalevolve.v2.goal-contract.v1"
    contract_id: str = ""

    def __post_init__(self) -> None:
        if not self.metrics:
            raise ValueError("GoalContract requires at least one metric")
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("GoalContract metric names must be unique")
        if not self.contract_id:
            body = asdict(self)
            body["contract_id"] = ""
            object.__setattr__(self, "contract_id", f"GC_{sha256_json(body)[:16]}")

    def evaluate(self, metrics: Mapping[str, Any]) -> tuple[float, dict[str, float | None], list[str]]:
        residuals: dict[str, float | None] = {}
        missing: list[str] = []
        weighted = 0.0
        total_weight = 0.0
        for spec in self.metrics:
            raw = metrics.get(spec.name)
            try:
                value = None if raw is None else float(raw)
            except (TypeError, ValueError):
                value = None
            residual = spec.residual(value)
            residuals[spec.name] = residual
            if residual is None:
                missing.append(spec.name)
                # Missing data cannot be compensated by unrelated QoR terms.
                weighted += spec.weight
                total_weight += spec.weight
                continue
            weighted += spec.weight * residual
            total_weight += spec.weight
        return weighted / max(total_weight, 1e-12), residuals, missing

    def hard_violations(self, metrics: Mapping[str, Any]) -> list[str]:
        violations: list[str] = []
        for spec in self.metrics:
            if not spec.hard:
                continue
            residual = spec.residual(_number(metrics.get(spec.name)))
            if residual is None:
                violations.append(f"missing_hard_metric:{spec.name}")
            elif residual > 0:
                violations.append(f"hard_target_not_met:{spec.name}")
        return violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "design": self.design,
            "baseline_metrics": self.baseline_metrics,
            "metrics": [asdict(metric) for metric in self.metrics],
            "source_fingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoalContract":
        return cls(
            design=str(data["design"]),
            baseline_metrics={key: float(value) for key, value in dict(data["baseline_metrics"]).items()},
            metrics=tuple(MetricSpec(**item) for item in data["metrics"]),
            source_fingerprint=dict(data.get("source_fingerprint") or {}),
            schema_version=str(data.get("schema_version") or "goalevolve.v2.goal-contract.v1"),
            contract_id=str(data.get("contract_id") or ""),
        )


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def build_contract(
    *,
    design: str,
    baseline_metrics: Mapping[str, float],
    target_metrics: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
    hard_metrics: set[str] | None = None,
    maximize_metrics: set[str] | None = None,
    source_fingerprint: Mapping[str, str] | None = None,
) -> GoalContract:
    """Build once from a measured baseline and explicit absolute targets."""
    weights = weights or {}
    hard_metrics = hard_metrics or set()
    maximize_metrics = maximize_metrics or set()
    decision_baselines = {
        name for name in baseline_metrics
        if name.lower() not in OBSERVER_ONLY_METRICS
    }
    decision_targets = {
        name for name in target_metrics
        if name.lower() not in OBSERVER_ONLY_METRICS
    }
    missing_targets = decision_baselines - decision_targets
    unexpected_targets = decision_targets - decision_baselines
    if missing_targets or unexpected_targets:
        details: list[str] = []
        if missing_targets:
            details.append(f"missing target_metrics for {sorted(missing_targets)}")
        if unexpected_targets:
            details.append(f"target_metrics without a baseline for {sorted(unexpected_targets)}")
        raise ValueError("goal metric names must match baseline_metrics: " + "; ".join(details))
    specs: list[MetricSpec] = []
    for name, baseline in baseline_metrics.items():
        if name.lower() in OBSERVER_ONLY_METRICS:
            continue
        minimize = name not in maximize_metrics
        specs.append(
            MetricSpec(
                name=name,
                baseline=float(baseline),
                target=float(target_metrics[name]),
                weight=float(weights.get(name, 1.0)),
                minimize=minimize,
                hard=name in hard_metrics,
            )
        )
    return GoalContract(
        design=design,
        baseline_metrics={
            key: float(value)
            for key, value in baseline_metrics.items()
            if key.lower() not in OBSERVER_ONLY_METRICS
        },
        metrics=tuple(specs),
        source_fingerprint=dict(source_fingerprint or {}),
    )
